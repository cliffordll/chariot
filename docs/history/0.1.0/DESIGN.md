# Chariot 架构设计(v0)

> **定位**:本机跑的智能体(agent)server —— 不是代理、不是翻译层、不是网关。
> 用户通过三种业界主流 API 格式(Anthropic Messages / OpenAI Chat Completions /
> OpenAI Responses)和它对话,server 内部自己生成响应。
> 默认 fallback 到内置 `MockModel`(本地 echo),将来挂真实模型是一层 `Model` 实现的替换。

---

## 1. 一句话总结

**Chariot = 本机 HTTP server → Agent → Model(默认 Mock)**,三者通过两条窄接口解耦:

- `Controller → Agent.handle(protocol, body)` —— 只管 HTTP 粘合
- `Agent → Model.respond(protocol, body, *, stream)` —— 只管"输入 bytes → 响应"

不存在"上游"、"代理"、"翻译"、"透传"的概念。

---

## 2. 分层

```
┌───────────────────────────────────────────────────────────────────┐
│  客户端:CLI / Web UI / Tauri 桌面 / 任何 HTTP 客户端                │
└──────────────────────────────┬────────────────────────────────────┘
                               │ HTTP
                               ▼
          POST /v1/messages           POST /v1/chat/completions
                                      POST /v1/responses
                               │
                               ▼
┌───────────────────────────────────────────────────────────────────┐
│  Controller (dataplane.py)                                          │
│   读 body;按 URL 映射 protocol;get_agent().handle(protocol, body) │
└──────────────────────────────┬────────────────────────────────────┘
                               ▼
┌───────────────────────────────────────────────────────────────────┐
│  Agent (chariot/server/agent.py)                                    │
│  - 单例,lifespan startup 里 init_agent()                            │
│  - handle(): 探测 stream flag → model.respond() → 记一条 log        │
│  - 未来可在这层加:多轮记忆、工具调用、自我进化循环、model 路由       │
└──────────────────────────────┬────────────────────────────────────┘
                               ▼
┌───────────────────────────────────────────────────────────────────┐
│  Model Protocol (chariot/server/model/base.py)                      │
│  - 无状态的响应生成器接口                                            │
│  - respond(protocol, body, *, stream) -> Response                   │
│  - 不碰 DB、不记 log、不感知 "上游"                                  │
└──────────────────────────────┬────────────────────────────────────┘
                               ▼
                  ┌─────────────┴─────────────┐
                  │                           │
           ┌──────────────┐           ┌──────────────────┐
           │  MockModel   │           │  (将来) 真模型   │
           │  三协议手写   │           │  本地 llama / 云  │
           │  echo 响应    │           │  LLM adapter 等   │
           └──────────────┘           └──────────────────┘

                              (DB · 只记 logs)
                  ┌──────────────────────────────┐
                  │  logs: id / model / status / │
                  │  latency / error / created   │
                  └──────────────────────────────┘
```

---

## 3. 三协议端点(不做翻译,只做格式对话)

chariot 同时承认三种业界主流 API schema。Model 实现决定自己怎么回答每种协议。

| 端点 | 协议 | 请求 schema | 响应 schema |
|---|---|---|---|
| `POST /v1/messages` | Anthropic Messages | `{model, max_tokens, messages: [{role, content}], stream?}` | `{id, type: "message", role: "assistant", content: [{type: "text", text}], stop_reason, usage}` |
| `POST /v1/chat/completions` | OpenAI Chat Completions | `{model, messages, stream?, stream_options?}` | `{id, object: "chat.completion", choices: [{message, finish_reason}], usage}` |
| `POST /v1/responses` | OpenAI Responses | `{model, input, stream?}` | `{id, object: "response", status, output: [...], output_text, usage}` |

**关键**:这三个 endpoint 的 schema 由**上游生态定义**,chariot 不做跨格式翻译;但 `Model` 实现**必须**按请求 hit 的协议返回对应 schema。`MockModel` 为每个协议手写了最小响应(非流 + SSE 两份)。

SSE 各协议事件约定:
- Messages:`message_start` → `content_block_start` → N × `content_block_delta` → `content_block_stop` → `message_delta` → `message_stop`
- Chat Completions:逐 chunk 吐,末尾一个 `finish_reason: "stop"` 的空 delta,最后 `data: [DONE]`
- Responses:`response.created` → `output_item.added` → `content_part.added` → N × `output_text.delta` → `output_text.done` → `response.completed`

---

## 4. 数据面流程

```
client POST /v1/messages { model, stream, messages }
         │
         ▼
controller.messages(request):
    body  = await request.body()     # 原始 bytes
    return await get_agent().handle(Protocol.MESSAGES, body)
         │
         ▼
Agent.handle(protocol, body):
    t0 = monotonic()
    model_hint = _detect_model_hint(body)    # 仅作日志
    is_stream  = _detect_stream(body)
    try:
        resp = await self.model.respond(protocol, body, stream=is_stream)
        await log_writer.record(status="ok", model=model_hint, latency_ms=...)
        return resp
    except ServiceError as e:
        await log_writer.record(status="error", error=f"{e.code}: {e.message}", ...)
        raise
         │
         ▼
MockModel.respond(Protocol.MESSAGES, body, *, stream=True):
    _parse_body(body)               # JSON → dict;非法就 ServiceError(400)
    echo = _extract_echo_text(...)  # 最后一条 user 消息的纯文本
    reply = f"[mock echo] {echo}"
    return StreamingResponse(_messages_sse(reply), media_type="text/event-stream")
```

响应一路透传回 client,controller / Agent 不改 body,只追加 log。

---

## 5. Model 接口契约

`chariot.server.model.base.Model` 是 `typing.Protocol`,实现 checklist:

```python
class Model(Protocol):
    name: str  # 写入 logs.model;UI 展示
    async def respond(
        self,
        protocol: Protocol,
        body: bytes,
        *,
        stream: bool,
    ) -> Response: ...
```

三条硬约束:

1. **无状态**。不保存对话历史,不 last-seen;每次 respond() 独立。多轮在 body.messages 里,由客户端管理。
2. **不碰 DB**。Model 只做 "bytes → Response";日志由 Agent 记。
3. **不感知 "上游"**。Model 里不区分什么外部代理、什么 api_key。Model **就是**响应的源头。

将来加新 model(比如 `LocalLlamaModel`、`AnthropicAdapterModel`)就写一个实现这三条的类,`init_agent(model=...)` 换一下。

---

## 6. logs 表

唯一一张表。每次 `Agent.handle()` 落一条:

```sql
CREATE TABLE logs (
    id            TEXT PRIMARY KEY,           -- 32 char UUID4 hex
    model         TEXT,                       -- body.model 字段(客户端意向)
    input_tokens  INTEGER,
    output_tokens INTEGER,
    latency_ms    INTEGER,
    status        TEXT NOT NULL,              -- ok / error / timeout
    error         TEXT,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_logs_created_at ON logs(created_at);
```

- Agent 层在请求成功 / 失败 / 异常时都记。
- `LogWriter` 自己拿 session,不侵入 controller;写失败 warning 不上冒。

---

## 7. 管理面(/admin/*)

```
GET  /admin/ping       → {ok: true}
GET  /admin/status     → {version, uptime_ms, model, url}
POST /admin/shutdown   → graceful shutdown(响应先发,再 should_exit)
GET  /admin/logs       → list LogOut(limit / offset / since)
GET  /admin/stats      → {period, total_requests, success_rate, avg_latency_ms}
```

`/admin/status.model` 就是当前 Agent 的 `model.name`。Dashboard 用这个字段展示。

---

## 8. 单例 / lifespan

```python
# chariot/server/app.py 里
async with lifespan(_app):
    await init_db()
    init_agent()          # 默认 MockModel;v1+ 从 config 读
    yield
    reset_agent()
    await dispose_db()
```

`get_agent()` 是整个 server 唯一对外的 Agent 入口。Controller 不 import 具体 Agent 类、不 import 具体 Model 类。

---

## 9. 测试

| 文件 | 覆盖 |
|---|---|
| `tests/server/test_model_mock.py` | MockModel 三协议 schema + SSE 事件 + 错误路径 |
| `tests/server/test_agent.py` | Agent.handle 转发契约 + 日志落地 + factory / singleton |
| `tests/server/test_dataplane.py` | 3 endpoint → Agent 的 protocol 映射 + body 透传 + 默认 Mock 端到端 |
| `tests/server/test_admin.py` | /admin/ping、/admin/status、/admin/logs since 游标 |
| `tests/sdk/test_client_admin.py` | ProxyClient admin 方法 |
| `tests/cli/test_commands.py` | CLI 子命令注册 + help |
| `tests/sdk/test_discover.py` | server 发现 / 启动协议 |

---

## 10. 未来扩展(v1+,不在当前范围)

- **真实模型**:写 `chariot/server/model/<name>.py` 实现 Model,启动时按 config 注入
- **多轮记忆**:Agent 里加 `Conversation` 存 history,model 变成"按轮生成"
- **工具调用**:Agent 在 respond 后检查 tool_use,执行工具,再调 model 补响应
- **进化循环**:Agent 可以持有多个 model 候选,根据 logs 的 feedback 调权重 / 切换
- **多 agent**:不再单例,按 agent_id 路由;logs 加 agent_id 字段

这些都**不改** `Controller` 和 `Model` 的接口,都在 Agent 层。

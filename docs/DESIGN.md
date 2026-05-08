# Chariot 架构设计(0.6.5)

> **当前版本**:`0.6.5`(开发中,基于 0.6.0 架构修正)
> **上一版归档**:[`docs/history/0.5.0/DESIGN.md`](history/0.5.0/DESIGN.md)
>
> **0.6.5 主题(增量)**:对照 hermes-agent 修正 0.6.0 的"长跑场景架构盲区"。
> 核心变化:**Provider 不再持 httpx client**(升 `ClientCache` 进程级共享)+
> **AIAgent 撤单例**(改 `AgentRegistry` per-session)+ **`ChatRequest.model`
> per-call 字段加回**。详 §5(整章重写)+ §3.1。Surface / 工具 / DB schema
> 全部不变。
>
> **0.6.0 主题**:**架构定位扭转 —— AIAgent 库化 + 协议无关内核 + 目录全面重组**。
> chariot 从"本机 server(对外暴露 Anthropic Messages 协议)"扭转为
> "**自演化 CLI agent**(对标 hermes-agent),跑在 VPS / 本地 / 云,
> 多个 surface(CLI / Sidecar / Gateways / ACP / MCP)各自启进程内 import AIAgent
> 直接构造,共享 `~/.chariot/chariot.db`"。
>
> **核心变更**(vs 0.5.0):
> 1. **撤 `chariot/server/`**:整套 fastapi server + `/v1/messages` Anthropic 协议
>    代理层退役。chariot 不再扮演"长得像 Anthropic 后端"的角色
> 2. **顶层目录全面重组**:`agent/` 缩成"狭义内核"(只装 AIAgent 跑起来必需的运行时);
>    `tools/` / `providers/` / `repos/` / `database/` 平铺顶层为功能模块;Surface
>    各自独立目录(`cli/` / `sidecar/`,后续加 `gateways/` / `acp/` / `mcp/`);
>    `rpc/` 装跨进程通信框架(stdio JSON-RPC,共享给 sidecar / acp / mcp)
> 3. **撤 `chariot/sdk/`**:SDK 客户端(ProxyClient / ChatStream)是"调 server"
>    的产物,库化后调用方直接 `from chariot.agent.run import AIAgent` 即可
> 4. **撤 `chariot/shared/`**:`shared/sse.py` 移入 `chariot/providers/_sse.py`
>    (它的唯一用户是 AnthropicProvider)
> 5. **新增 `chariot/sidecar/`**:Tauri 桌面壳的 Python sidecar(原计划 tui_gateway,
>    在 chariot 语境字面误导,改名);通过 stdio JSON-RPC 双向通信,Tauri 前端 ↔
>    sidecar 的数据流不再走 HTTP
> 6. **新增协议无关核心类型 `ChatRequest` / `ChatEvent`**:AIAgent 的输入 / 输出。
>    AIAgent 内核不知道任何 wire format(SSE / JSON-RPC),编码由 surface 层各自负责
> 7. **`Provider` 抽象**(替代旧 `Model`):产 `AsyncIterator[ChatEvent]`。
>    AnthropicProvider / MockProvider 等。`ToolLoop` 同步改名 `AgentLoop`
>    (撤 fast/slow path 二分后,这就是 chat 主循环本体)
> 8. **撤 `chariot/server/runtime/`**:`endpoint.json` / `spawn.lock` / watcher
>    这套"单实例 daemon 锁"机制,在库化模式下不再需要(每个 surface 进程独立)
> 9. **撤 daemon 时代 CLI 命令**:`chariot start` / `chariot stop` 撤;`chariot
>    status` 改语义保留(显示 DB 路径 / provider 数 / tool 数 / 版本);`chariot
>    stats` 保留
>
> **不变的事**:BaseTool / ToolRegistry / 4 内置工具、ConvoRepo /
> ConvoLockManager 单进程内的 per-conv 串行(库化后转为"per-conv +
> per-process",跨进程并发由 SQLite 串行写保护)、表 schema(`conversations` /
> `messages` / `tools` / `models` / `logs`)。

---

## 1. 一句话总结

chariot 是一个 **自演化 CLI agent**:`AIAgent` 是可被任何进程 import 的纯 Python
库,Provider 抽象 + agent loop + 持久化全在库内;CLI / Sidecar / Gateways / ACP /
MCP 等多种 surface 各自启动独立进程,内部直接构造 AIAgent 实例,通过共享
`~/.chariot/chariot.db` 互相看到对方写入的对话与状态。

## 2. 进程模型

```
   ┌─────────────────────────┐    ┌─────────────────────────┐
   │ chariot chat (CLI)      │    │ chariot gateway start    │
   │ ─────────────────────── │    │ ─────────────────────── │
   │ from chariot.agent       │    │ from chariot.agent       │
   │   .run import AIAgent   │    │   .run import AIAgent   │
   │ agent = AIAgent.from_db │    │ agent = AIAgent.from_db │
   │ events = agent.run(req) │    │ # bridge → Telegram /   │
   │ → terminal renderer     │    │       Discord / ...     │
   │                         │    │ (long-live process)     │
   └────────────┬────────────┘    └────────────┬────────────┘
                │                                │
                │  共享                           │  共享
                ▼                                ▼
        ┌────────────────────────────────────────────────┐
        │  ~/.chariot/chariot.db (SQLite + WAL)          │
        │  conversations / messages / tools / models /   │
        │  logs / (0.7.0+) skills / memory               │
        └────────────────────────────────────────────────┘
                ▲                                ▲
                │  共享                           │  共享
                │                                │
   ┌────────────┴────────────┐    ┌────────────┴────────────┐
   │ Tauri Desktop           │    │ ACP / MCP (0.10.0+)     │
   │ ─────────────────────── │    │ ─────────────────────── │
   │  Tauri (Rust + WebView) │    │ stdio JSON-RPC          │
   │      ↕ stdio JSON-RPC   │    │   ↕                     │
   │  chariot.sidecar        │    │  chariot.acp / chariot.mcp │
   │  (Python)               │    │  (Python)               │
   │  → AIAgent inside       │    │  → AIAgent inside       │
   └─────────────────────────┘    └─────────────────────────┘
```

**关键约定**:
- **AIAgent 实例属于进程**:每个 surface 进程构造,生命周期跟进程同步
- **状态共享靠 DB**:跨进程同步通过 SQLite 串行写 + advisory lock(详见 §7)
- **同进程内 per-conv 串行**:`ConvoLockManager`(asyncio.Lock 字典)保
  单进程内同一 convo_id 的 agent loop 不会被并发请求穿插
- **跨进程并发**:用 SQLite WAL + `BEGIN IMMEDIATE` 短事务保护"load history +
  append message"原子段。0.5.0 的 in-memory asyncio.Lock 升级为"in-memory + DB"
  双层(详见 §7.2)
- **多 surface 共享 stdio JSON-RPC 框架**:sidecar / acp / mcp 三个 surface 的
  wire format 同款(stdio newline-delimited JSON),框架代码集中在
  `chariot/rpc/jsonrpc.py`,各 surface 只写自己的方法 dispatch 业务

> **设计取舍**:为什么不上集中 daemon?(对标 hermes 的最终选择)
> 1. **简单性优先**:库式 AIAgent 没有 IPC schema 演化负担;每个 surface 直接
>    消费 Python 对象,refactor 阻力最低
> 2. **远程访问已被 SSH 解决**:VPS 模式用 `ssh user@vps -- chariot chat` 即可,
>    daemon 给的额外价值有限,但维护成本(协议演化 / 单实例锁 / 进程监督)实在
> 3. **多 surface 并发场景实际很稀疏**:同时 `chariot chat` + Telegram 推消息
>    的并发,SQLite 串行就够了;真要高 QPS 跑 chariot,反而是把它当 hermes 的
>    `gateway --platform api_server` 用,后续版本再做

## 3. 核心抽象 —— `ChatRequest` / `ChatEvent`

**AIAgent 内核协议无关**:输入是 `ChatRequest`,输出是 `AsyncIterator[ChatEvent]`。
SSE / JSON-RPC / 任何 wire format 都不在 AIAgent 内核可见范围。

### 3.1 `ChatRequest`(`chariot/agent/chat_request.py`)

**设计依据**:**结构**跟 Claude Messages API 的 request body 1:1 对应(messages
content 形态 / tool_use / tool_result / role 词表 / sampling 字段命名),
理由跟 ChatEvent 一致——降低学习成本,AnthropicProvider 实现尽量简单。
**唯一命名偏离**:chariot 路由用的字段叫 `provider_name`,而不是 wire 字段名
`model`(避免一个名字承担两种语义)。

```python
@dataclass(frozen=True)
class ChatRequest:
    # ─── chariot 路由字段(必填) ───
    provider_name: str                            # entry name(chariot 内部当 Provider 路由 key)
    # ─── Claude Messages API 字段(顺序按官方 spec) ───
    messages: list[Message]
    model: str | None = None                      # 0.6.5+ per-call LLM id 覆盖;
                                                  # None = 用 entry.options.model
    max_tokens: int = 4096
    system: str | list[SystemBlock] | None = None
    tools: list[ToolSchema] | None = None         # None = 沿用 enabled tools 注入;
                                                  # [] = 关闭工具调用
    tool_choice: dict | None = None               # {"type": "auto"|"any"|"tool"|"none"}
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    stop_sequences: list[str] | None = None
    metadata: dict | None = None                  # {"user_id": "..."}
    thinking: dict | None = None                  # extended thinking 配置(Claude 4+)
    # ─── chariot 扩展字段(顶层放,跟 Claude 字段不冲突) ───
    convo_id: str | None = None                   # None = stateless;ULID = stateful
    agent_id: str | None = None                   # 0.9.0+ 多 AIAgent 实例;0.6.0 默认 None
```

**`Message` / `SystemBlock` / `ToolSchema` 也跟 Claude 形态 1:1**:

```python
@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant"]
    content: str | list[ContentBlock]   # 字符串简写 OR content block 数组

@dataclass(frozen=True)
class SystemBlock:
    type: Literal["text"]
    text: str
    cache_control: dict | None = None    # {"type": "ephemeral"} 等

@dataclass(frozen=True)
class ToolSchema:
    name: str
    description: str
    input_schema: dict                   # JSON Schema
```

`ContentBlock` 是 union,涵盖 Claude 协议的全部 block 类型(text / image /
tool_use / tool_result / thinking / document 等),由 `type` 字段区分。

**关于 `provider_name` vs `model`**:Claude API wire 字段叫 `model`,装的是
LLM 真实 id(如 `claude-sonnet-4-6`);chariot 在 IR 这层把路由 key 单独命名为
`provider_name`,装 entry name(用户在 `providers` 表里的命名,如 `claude` /
`mock` / `ollama-qwen`)。AIAgent 用 `req.provider_name` 路由到对应 Provider
实例,Provider 内部按 `req.model or self.config.model` 决定写到 wire body 的
`model` 字段。**`req.provider_name`(路由 key)≠ `req.model`(per-call LLM id)
≠ wire `body.model`(最终发出去的)**。

**`model` 字段(0.6.5+,可选)**:per-call LLM id 覆盖,默认 `None`。CLI
`--model` flag 走这条 —— `ChatContext.model_override` → `ChatRequest.model` →
Provider `body["model"] = req.model or self.config.model`。设计动机:`--model`
切 LLM id 不影响 (base_url, api_key) → ClientSpec 不变 → 不重建 httpx client
(零客户端开销);0.6.5 之前用的 `provider_overrides` 路径会重建 Provider 实例,
对纯切 LLM id 是过度操作。

**命名约定**:CLI flag 用短名 `--provider` / `--model`(贴近用户);IR / 内部
参数传递用 `provider_name` / `model`。ChatContext / ChatRequest 内部数据结构
都遵循。

**不引入的 Claude 字段**:
- `stream`:chariot 内核固定流式(`Provider.generate` 总是 `AsyncIterator`),
  非流式由 surface 自己聚合;不暴露给用户
- `service_tier`:Anthropic 计费层级,chariot 不暴露(用户在 Provider options
  里配)
- `anthropic_version` / `anthropic_beta`:HTTP header 级,`AnthropicProvider`
  内部处理,不进 ChatRequest

**chariot 扩展的两个字段**(放最后,跟 Claude 字段不冲突):
- `convo_id`:0.4.0 起 stateful 多轮触发(0.6.0 起从
  `X-Chariot-Convo` header 升级到顶层字段)
- `agent_id`:0.9.0+ 多 AIAgent 实例路由;0.6.0 默认 `None`,字段先占位

**OpenAIProvider 怎么对接 ChatRequest**(0.7.0,作为非 Claude Provider 的范例):
- `provider_name` → 跟 AnthropicProvider 一样,只是路由 key,不进 wire body;
  body.model 由 OpenAIProvider 自己从 `self.config.model` 写
- `messages` → OpenAI 形态略不同(content 是字符串而非 block 数组),Provider 内部翻译
- `max_tokens` / `temperature` / `top_p` → OpenAI 同名,直接传
- `top_k` → OpenAI 不支持,Provider 忽略(或严格模式报错)
- `stop_sequences` → OpenAI 叫 `stop`(单数),Provider 改名
- `tools` / `tool_choice` → OpenAI 形态相近但 schema 细节不同,Provider 翻译
- `metadata` → OpenAI 没对应(可塞 `user` 字段),Provider 翻译
- `thinking` → OpenAI 不支持,Provider 忽略

`ChatRequest` 是"协议无关 IR 但形态选 Claude" —— Claude Provider 透传,其它
Provider 翻译。

### 3.2 `ChatEvent`(`chariot/agent/chat_event.py`)

**设计依据**:**直接采用 Claude Messages API 的流式 SSE event 类型 + payload 字段
1:1 对应**,不做内部规范化重命名。学习曲线 = 读一遍 Anthropic streaming 文档。

理由:
1. **降低学习成本**:任何熟悉 Claude API 的开发者读 ChatEvent 零障碍
2. **AnthropicProvider 实现极简**:几乎是 SSE 帧 → typed dataclass 的纯翻译,
   payload 字段名不变
3. **Tauri / sidecar JSON-RPC 序列化也 1:1**:`chat_event` notify payload 跟
   Anthropic SSE 同结构,前端原生熟
4. **OpenAIProvider(0.7.0)的目标也明确**:把 OpenAI 协议翻成"Claude 形态",
   而不是"chariot 自家形态"

discriminated union(用 `kind: Literal[...]` 字段做区分):

```python
@dataclass(frozen=True)
class ChatEvent:
    kind: Literal[
        # ─── Claude SSE 原生 event 类型(8 种) ───
        "message_start",         # 一次 message 起头(message_id / model / 初始 usage)
        "content_block_start",   # content block 开始(index / content_block:
                                 #   type=text|tool_use|thinking|...)
        "content_block_delta",   # content block 增量(index / delta:
                                 #   type=text_delta|input_json_delta|thinking_delta|...)
        "content_block_stop",    # content block 收尾(index)
        "message_delta",         # message 整体 delta(delta.stop_reason / usage)
        "message_stop",          # 一次 message 结束
        "ping",                  # keepalive(消费方可忽略)
        "error",                 # 错误(error.type / error.message)
        # ─── chariot 自注入(Claude SSE 没有) ───
        "tool_result",           # 工具执行完(对应 Claude user msg 里的 tool_result
                                 #   content block;chariot 把它作为事件流出来,
                                 #   UI 才能实时看到工具卡片)
        "stream_done",           # 整个 agent run 收敛(可能跨多轮 message_stop;
                                 #   chariot 跨轮概念,Anthropic 协议无此事件)
    ]

    # 各 kind 专属字段(全部 optional,按 kind 取用)
    message: dict | None = None          # message_start
    index: int | None = None             # content_block_*
    content_block: dict | None = None    # content_block_start(type / id / name / ...)
    delta: dict | None = None            # content_block_delta / message_delta
    usage: dict | None = None            # message_start / message_delta
    error_type: str | None = None        # error
    error_message: str | None = None     # error
    # tool_result 用(跟 Claude tool_result content block 同字段)
    tool_use_id: str | None = None
    content: str | list[dict] | None = None
    is_error: bool = False
    # stream_done / ping 无附加字段
```

**多轮序列样例**:

纯 text 单轮:
```
message_start
content_block_start (index=0, text)
content_block_delta+ (text_delta × N)
content_block_stop (index=0)
message_delta (stop_reason=end_turn, usage)
message_stop
stream_done
```

含一次工具调用(2 轮):
```
message_start
content_block_start (index=0, text)
content_block_delta+ (text_delta × N)
content_block_stop (index=0)
content_block_start (index=1, tool_use, id=toolu_..., name=read_file)
content_block_delta+ (input_json_delta × N)
content_block_stop (index=1)
message_delta (stop_reason=tool_use, usage)
message_stop
tool_result (tool_use_id=toolu_..., content="...", is_error=false)   ← chariot 注入
message_start                                                         ← 第 2 轮
content_block_start (index=0, text)
content_block_delta+ (text_delta × N)
content_block_stop (index=0)
message_delta (stop_reason=end_turn, usage)
message_stop
stream_done
```

错误形态:
```
error (error_type=overloaded_error, error_message=...)
```

> **设计原则**:`ChatEvent` 用平铺 union 而非 ABC + 子类。理由:
> 消费方多是 `match event.kind: case "content_block_delta": ... case "message_delta":
> ...` 形态,平铺 dataclass 比 isinstance dispatch 简洁,序列化(给 Tauri JSON-RPC)
> 也更直接

> **关于扩展**:Claude API 后续若加新 event type(如 extended thinking 的
> `signature_delta` 已经在用),chariot 直接加 `kind` literal 值即可,字段
> optional 不破坏既有消费方。chariot 自注入的两个(`tool_result` / `stream_done`)
> 跟 Claude SSE event 命名空间不冲突(Claude 协议无此名)。

### 3.3 `AIAgent.run` 接口

```python
class AIAgent:
    async def run(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        """跑一次 chat,产 ChatEvent 流。

        - stateful(req.convo_id 非空):内部 load history + persist
        - 工具调用:AIAgent 内部跑工具 + inject tool_result event,客户端只消费 events
        - 收敛:产 stream_done event 后退出
        """
```

## 4. 分层

```
              Surface 层(进程入口 + wire codec)
              ┌──────┬──────────┬─────────┬─────┬─────┐
              │ CLI  │ Sidecar  │ Gateways│ ACP │ MCP │
              │      │ (Tauri)  │ (0.8.0) │(0.10)│(后续)│
              └──┬───┴────┬─────┴────┬────┴──┬──┴──┬──┘
                 │  ChatRequest in     │      │      │
                 │  ChatEvent out      │      │      │
                 ▼                     ▼      ▼      ▼
              ┌────────────────────────────────────────┐
              │ AIAgent 内核(chariot/agent)           │
              │  ─ run.py(AIAgent)               │
              │  ─ loop.py(AgentLoop)             │
              │  ─ chat_request.py / chat_event.py      │
              │  ─ exceptions.py / config.py            │
              │  ─ convo_lock.py                 │
              └────────────────┬───────────────────────┘
                               │ 调用功能模块
                ┌──────────────┼──────────────┐
                ▼              ▼              ▼
          ┌──────────┐  ┌──────────┐  ┌──────────┐
          │providers/│  │ tools/   │  │ repos/   │
          │ base     │  │ base     │  │ *_repo   │
          │ registry │  │ registry │  │ log_writer│
          │ prober   │  │ builtin/ │  └──────────┘
          │ _sse     │  │  4 tools │         │
          │ builtin/ │  └──────────┘         ▼
          │  mock    │                 ┌──────────┐
          │  anthropic│                 │database/ │
          └──────────┘                 │  session │
                  │                    │  ORM     │
                  ▼                    └──────────┘
            上游 LLM API
```

跨 surface 共享的 wire format 框架在 `chariot/rpc/`(JSON-RPC for sidecar / acp / mcp)。

## 5. Provider 接口契约 + 四层生命周期(0.6.5 重写)

> **0.6.5 主题**:对照 hermes-agent 重新审视 Provider / client / Agent 的生命周期
> 边界。**核心:Provider 不再持 httpx client**,client 升到进程级 `ClientCache`
> 共享(LRU + 并发安全);AIAgent 撤单例,改 per-session `AgentRegistry`。
> per-call `--model` 切 LLM id 不动 client / 不动 Provider,零客户端开销;
> per-call `--base-url` / `--api-key` 改连接维度,走 ClientSpec 命中复用。

### 5.0 四层生命周期

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 4 — Surface(per-call / per-session,业务方决定)       │
│   CLI 进程 / sidecar 长跑 / Gateway listener / ACP / MCP     │
│   构造 `session_key`(CLI 用 "process";Gateway 用 chat_id;   │
│   Telegram 用 user_id 等),向下要 AIAgent                    │
└─────────────────────────────────────────────────────────────┘
                              │ acquire(session_key, ...)
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 3 — `AgentRegistry`(per-session,LRU 32)             │
│   ClassVar OrderedDict[session_key → AIAgent] + asyncio.Lock│
│   首次 acquire(session_key) → 装载 AIAgent + cache;后续命中  │
│   provider_overrides 仅首次 acquire 时 merge 进 entry.options │
└─────────────────────────────────────────────────────────────┘
                              │ AIAgent.from_db(db_path,
                              │                provider_overrides=...)
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 2 — `BaseProvider` 实例(per-session,挂 AIAgent 上)   │
│   self.config / self._options / self._spec(ClientSpec)     │
│   **不持 httpx client**;generate(req) 内部按需从 ClientCache  │
│   .get(self._spec) 拿 client                                 │
└─────────────────────────────────────────────────────────────┘
                              │ ClientCache.get(spec)
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 1 — `ClientCache`(进程级,LRU 16)                     │
│   ClassVar OrderedDict[ClientSpec → httpx.AsyncClient]      │
│   + asyncio.Lock。spec 命中 → 复用 client(连接 keepalive);  │
│   未命中 → 构造 + 缓存。**evict 不 aclose**(避免中断 in-flight)│
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
                          上游 LLM API
```

**生命周期粒度**:

| 层级 | 谁建 | 何时建 | 何时销 |
|---|---|---|---|
| Layer 1 client | `ClientCache.get` 首次未命中 | 进程内首次需要某个 ClientSpec | 进程退出 / `aclose_all` 显式调 |
| Layer 2 Provider 实例 | `ProviderRegistry.build` | 每个 AIAgent 装载时 | AIAgent GC 时(随 session 退出) |
| Layer 3 AIAgent | `AgentRegistry.acquire` | 每个 session_key 首次 acquire | LRU evict / `release` / `aclose_all` |
| Layer 4 surface | 业务方 | 业务事件触发(CLI 启动 / 用户消息到达) | 业务方决定 |

**关键性质**:
- **Provider 重建≠client 重建**:`--base-url` 改 → Provider 实例重建 →
  ClientSpec 重算 → `ClientCache.get` 命中既有 client(同 spec 用过)或新建。
  连接池随 spec 复用,不会因为 Provider 实例 churn 而断
- **session 间互不影响**:Gateway 同时跑 100 个 chat session,各自独立
  AIAgent + 各自 entry.options;但底下的 httpx client 共享(同 spec 池化)
- **零静默状态**:模块级零自由函数 + 零可变变量(CLAUDE.md ⭐),所有状态挂在
  类的 ClassVar 上,生命周期由 classmethod 管(`acquire` / `aclose_all`)

### 5.1 `BaseProvider`(`chariot/providers/base.py`)

```python
@dataclass(frozen=True)
class BaseProviderConfig:
    """Provider 通用配置(子类可继承加专属字段)。"""
    name: str           # entry name(用户写的,如 "claude" / "mock")
    model: str          # 上游真实 model(给 LLM API)

class BaseProvider(ABC):
    """Provider 抽象基类。具体实现见 providers/builtin/。

    并发约束:实例所有字段构造后 immutable;`generate(req)` 不在 self 上挂
    per-request mutable state。同实例可被多个 coroutine 并发调 generate
    (如 Gateway 高并发场景)。
    """
    config: BaseProviderConfig

    @classmethod
    @abstractmethod
    def from_options(cls, options: dict[str, Any]) -> Self:
        """从 ProviderEntry.options 构造;options 不合法 raise ConfigError。"""

    @abstractmethod
    async def generate(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        """跑一次 LLM 请求,产 ChatEvent 流(message_start / content_block_* /
        message_delta / message_stop / error / ping)—— Claude SSE 形态。

        Provider 子类不知道工具循环、不知道 Conversation、不写 DB —— 纯输入输出。
        OpenAIProvider / LocalLlamaProvider 等也产同一份形态,内部翻译。
        """
```

**命名约定**:抽象基类用 `Base*` 前缀(`BaseProvider` / `BaseTool` / `BaseGateway`
/ `BaseSkill`),具体子类不带前缀(`AnthropicProvider` / `ReadFileTool` 等)。
文件名沿用 `base.py` 惯例,文件里装该层的 base class。

**vs 0.6.0**:Provider 不再 `__init__(*, config, api_key, base_url, client)` —
改 `__init__(*, config, options)`,client 由 `ClientCache.get(self._spec)`
按需取。0.6.0 的 `aclose()` ABC 撤(Provider 不再持有可关闭资源)。

### 5.2 `ClientCache` + `ClientSpec`(`chariot/providers/clients.py`)

```python
@dataclass(frozen=True)
class ClientSpec:
    """httpx 客户端的 cache key。frozen + hashable;同 spec 共享 client。"""
    provider_type: str                              # "anthropic" / "openai" / ...
    base_url: str
    api_key: str                                    # auth header value
    headers: tuple[tuple[str, str], ...]            # 完整 headers(含 api_key)
    max_connections: int = 20
    max_keepalive: int = 10
    connect_timeout_sec: float = 10.0
    read_timeout_sec: float = 300.0
    write_timeout_sec: float = 30.0
    pool_timeout_sec: float = 10.0

    def build(self) -> httpx.AsyncClient: ...

class ClientCache:
    """进程级 httpx 客户端缓存(LRU 16,asyncio.Lock 并发安全)。

    设计:Provider 实例可被 per-call override 重建,但底下 httpx client
    要尽量复用(连接 keepalive 是 LLM API 请求的主要延迟优化点)。
    `(base_url, api_key)` 维度变 → 新 spec → 新 client;同维度变 → 命中既有 client。
    """
    _MAX_VARIANTS: ClassVar[int] = 16
    _cache: ClassVar[OrderedDict[ClientSpec, httpx.AsyncClient]] = OrderedDict()
    _lock: ClassVar[asyncio.Lock] = asyncio.Lock()

    @classmethod
    async def get(cls, spec: ClientSpec) -> httpx.AsyncClient: ...

    @classmethod
    async def aclose_all(cls) -> None: ...
```

**evict 不 aclose 的理由**:超 16 个 variant 时 LRU 弹最老,但**不**调
`client.aclose()`。如果有 in-flight 请求拿着这个 client,中途关连接会污染流式
契约。被 evict 的 client 由 GC 回收(等所有引用释放);进程退出靠 `aclose_all`
显式 close 全部。

### 5.3 `AnthropicProvider`(`chariot/providers/builtin/anthropic.py`)

- 用 httpx 调上游 `/v1/messages`,**body 构造而非透传**(从 `ChatRequest` 拼请求)
- **`body["model"] = req.model or self.config.model`**(0.6.5+):per-call
  `req.model`(CLI `--model` 注入)优先;回退 `self.config.model`(实例化时
  从 `entry.options.model` 落)。`provider_name`(路由 key)不进 wire body
- **优先级解析**(详 §5.6 三种 override 路径):
  - `api_key`:inline `entry.options.api_key` → `entry.options.api_key_env`
    指向的 env(默认 `ANTHROPIC_API_KEY`)
  - `base_url`:inline `entry.options.base_url` → `ANTHROPIC_BASE_URL` env →
    默认 `https://api.anthropic.com`(对齐 Anthropic Python SDK 约定)
  - CLI `--base-url` / `--api-key` 通过 `provider_overrides` 注入到 inline
    options(详 §5.6)
- 流式:从 `ClientCache.get(self._spec)` 拿 client → SseParser 消费 SSE 字节
  → yield `ChatEvent`
- 错误码映射:401/403 → upstream_auth_failed,429 → rate_limited,5xx →
  upstream_server_error,httpx 网络异常 → upstream_unreachable

### 5.4 `MockProvider`(`chariot/providers/builtin/mock.py`)

- 不走任何 HTTP,直接 `yield` Claude 形态的 ChatEvent 序列
- options 容忍任意字段(测试 / 默认 seed entry 用)

### 5.5 `OpenAIProvider`(0.7.0,`chariot/providers/builtin/openai.py`)

- OpenAI Chat Completions 兼容(覆盖 OpenRouter / Kimi / DeepSeek / z.ai
  / Xiaomi / NVIDIA NIM 等)
- 同样不持 client;构造 ClientSpec(provider_type="openai")向 `ClientCache`
  要 client。Anthropic / OpenAI 的 spec 不互相命中(provider_type 不同 → 不同
  cache key)
- 内部翻译 ChatRequest → OpenAI body / SSE event → ChatEvent

### 5.6 三种 override 路径(per-process / per-session / per-call)

`provider entry` 的核心字段(`model` / `base_url` / `api_key` / 其它 options)在
0.6.5 起按"override 维度"分流到三条路径,**每条路径触发的重建粒度不同**。

| Override 路径 | 触发器 | 改谁 | 谁重建 | 谁不变 |
|---|---|---|---|---|
| **per-process** | DB 改 entry.options | entry.options | 整个进程下次启动起的 Provider | DB / 已有进程 |
| **per-session** `provider_overrides` | CLI `--base-url` / `--api-key`、Gateway session 配置 | merged options(进 AgentRegistry.acquire) | session 内的 Provider 实例 | 其它 session;若 spec 同则连 client 都不变 |
| **per-call** `req.model` | CLI `--model`、ChatContext.model_override | 单次 ChatRequest.model 字段 | **零**(只走 wire body) | Provider / Client / Spec 全部不变 |

**为什么这么分**:

1. **`model` 切 LLM id**:不影响 (base_url, api_key) → 不影响 ClientSpec →
   不需要新 client;甚至不需要新 Provider 实例(同 base_url + api_key 的多个
   "model id" 切来切去本就是同 client 的不同 wire body)。走 `req.model`
   per-call 字段,零开销
2. **`base_url` / `api_key` 切连接维度**:必须重算 ClientSpec;Provider 实例
   也要重建(因为 `self._spec` 是 frozen);但底下 ClientCache 按 spec 命中,
   同 spec 已存在 → 复用 client,只是套了个新 Provider 壳
3. **DB entry 改**:per-process 改 —— 下次启动起的进程读新 options。已活着的
   AIAgent 不受影响(per-session 缓存住了)

**per-session override 链路**(CLI `chariot chat --base-url Y --api-key Z`):

```
CLI flag       (--base-url Y / --api-key Z;--model 不在这条路径!)
   │
   ▼
options patch  {"base_url": "Y", "api_key": "Z"}
   │
   ▼
provider_overrides = {"<provider_name>": patch}
   │
   ▼
AgentRegistry.acquire(session_key, db_path=..., provider_overrides=...)
   │ 首次 acquire 时把 overrides[name] merge 进 entry.options
   ▼
AIAgent.from_db(db_path, provider_overrides=...)
   │ 内部对每个 entry.name,merged_options = {**entry.options, **overrides[name]}
   │ → ProviderRegistry.build(type, merged_options)
   ▼
新 AnthropicProvider 实例 (self._spec 反映 merged base_url / api_key)
   │ generate 时 ClientCache.get(self._spec)
   ▼
命中或新建 httpx client(同 spec 已存在 → 复用)
```

**per-call override 链路**(CLI `chariot chat --model claude-haiku-4-5`):

```
CLI flag --model
   │
   ▼
ChatContext.model_override = "claude-haiku-4-5"
   │
   ▼
ChatRequest.model = "claude-haiku-4-5"   ← 每轮 _build_request 都带
   │
   ▼
AnthropicProvider._build_body:
   body["model"] = req.model or self.config.model
   │
   ▼
wire body 写入 "claude-haiku-4-5"
```

**两条路径同时触发**(`--model X --base-url Y`):per-session 重建 Provider
(spec 反映 Y),per-call 把 X 写进每轮 wire body。互不干扰。

#### 副作用 / 适用边界

1. **Provider 必须无 per-request mutable state**:`generate(req)` 不在 self
   上挂临时数据;同实例可并发跑 N 个 generate(Gateway 场景)
2. **per-session override 重建 Provider 但不必重建 client**:同 spec 命中
   `ClientCache`,连接 keepalive 保住
3. **per-call override 零重建**:`--model` 切来切去都不动 Provider / Client
4. **AIAgent 不参与 override 解释**:`provider_overrides` 在 `from_db` 装载时
   merge 进 entry.options,落到 Provider `__init__`;之后 AIAgent 只看
   `req.provider_name`(路由)+ `req.model`(per-call wire 覆盖)

#### AIAgent 的角色(零参与)

AIAgent **不参与** `base_url` / `api_key` 的解释。它只做三件事:

1. **路由**:按 `req.provider_name` 从 `self._providers` 挑出对应实例
2. **委托**:调 `provider.generate(req)`,流式 yield `ChatEvent`
3. **per-call model 透传**:`req.model` 跟着 ChatRequest 一路到 Provider,
   AIAgent 不看(透明)

`agent/run.py` 全文搜不到 `base_url` / `api_key` 的语义解释 —— 那是 Provider
内部细节。AIAgent 只看 entry name + ChatEvent。

加新 Provider 类型(`OpenAIProvider` / `LocalLlamaProvider` / ...)零侵入:
新写 `BaseProvider` 子类 + 在 `ProviderRegistry` 注册,自己处理各家的
base_url / api_key / auth 形态 / SSE 解析。

### 5.7 `AgentRegistry`(`chariot/agent/registry.py`)

```python
class AgentRegistry:
    """per-session AIAgent 缓存(LRU 32,asyncio.Lock 并发安全)。

    撤 0.6.0 的 AIAgent._current 单例 —— 单例会卡住"同进程多 session"场景
    (Gateway 同时服务多个 chat_id)。
    """
    _MAX_AGENTS: ClassVar[int] = 32
    _agents: ClassVar[OrderedDict[str, AIAgent]] = OrderedDict()
    _lock: ClassVar[asyncio.Lock] = asyncio.Lock()

    @classmethod
    async def acquire(
        cls,
        session_key: str,
        *,
        db_path: Path,
        provider_overrides: dict[str, dict[str, str]] | None = None,
    ) -> AIAgent:
        """同 session_key → 同实例;首次构造 + cache。

        provider_overrides 仅首次 acquire 时生效(cached agent 忽略后续 overrides)。
        """

    @classmethod
    async def release(cls, session_key: str) -> None: ...

    @classmethod
    async def aclose_all(cls) -> None: ...
```

**session_key 选择**(各 surface 各自决定):
- CLI:`"process"`(整进程一个 session)
- Telegram Gateway:`f"tg:{chat_id}"`
- Discord Gateway:`f"dc:{guild_id}:{channel_id}"`
- ACP:连接级 connection id
- MCP:同上

**LRU 不 aclose**:同 ClientCache,evict 不调 AIAgent 的 close —— 等 GC 处理。
进程退出靠 `aclose_all` 显式释放(CLI `installed_runtime` 的 finally 段)。

### 5.8 `ProviderProber`(`chariot/providers/prober.py`)

- 共享 utility:对任意 ProviderEntry 做 ping(发个最小 chat 请求验通)
- 给 `chariot provider probe <name>` CLI 用;独立路径,不进 ClientCache
  (probe 偶发 + entry 维度 override 多,缓存命中率低)

## 6. AIAgent 内核(`chariot/agent/run.py` + `loop.py`)

### 6.1 `AIAgent` 类(`chariot/agent/run.py`)

```python
class AIAgent:
    """AIAgent 主体。**0.6.5 起撤单例 + 撤 patch_provider_options**;
    生命周期由 `AgentRegistry`(per-session 缓存)管(详 §5.7)。

    并发约束:同实例可被多个 coroutine 并发调 run(...)
    (Gateway 高并发场景);run 内部不在 self 上挂 per-request mutable state。
    """
    def __init__(
        self,
        *,
        providers: dict[str, BaseProvider],   # entry_name → BaseProvider 子类
        tools: dict[str, BaseTool],           # tool_name → BaseTool 子类
        sessionmaker: async_sessionmaker[AsyncSession] | None = None,
        lock_manager: ConvoLockManager | None = None,
    ) -> None: ...

    @classmethod
    async def from_db(
        cls,
        db_path: Path,
        *,
        provider_overrides: dict[str, dict[str, str]] | None = None,
    ) -> Self:
        """从 `~/.chariot/chariot.db` 装载 ProviderEntry / ToolEntry,
        构造所有 Provider / Tool 实例,返就绪 AIAgent。

        `provider_overrides`(0.6.5+):per-session inline patch,形如
        `{"claude": {"base_url": X, "api_key": Y}}`。装载时对每个 entry,
        merged_options = {**entry.options, **overrides[entry.name]} 落到
        Provider.from_options。**注意**:`--model` 走 per-call ChatRequest.model
        路径,不在这里(详 §5.6)。

        通常由 `AgentRegistry.acquire` 调用,而不是业务方直接 from_db。
        """

    async def run(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        """主入口。"""
```

`AIAgent.run` 实现拆成两条路径(取消 0.5.0 fast/slow path 的二分,统一走一条):

```text
AIAgent.run(req):
    if req.convo_id:
        async with lock_manager.acquire(req.convo_id):
            yield from _run_with_conv(req)
    else:
        yield from _run_stateless(req)

_run_with_conv / _run_stateless 都委托给 AgentLoop:
    loop = AgentLoop(provider, tools, repo, convo_id)
    async for event in loop.run(req):
        yield event
```

### 6.2 `AgentLoop`(`chariot/agent/loop.py`)

替代 0.5.0 的 `_stream_tool_loop_body` + `_TurnState` + `_process_one_turn` +
`_emit_synthetic_tool_result_message` 这一坨。**单一职责**:消费 Provider events
+ 检测 tool_use + 跑工具 + 注 tool_result events + 续轮。

> **命名**:0.5.0 称 "tool loop" 是因为有 fast/slow path 的二分(slow path 才走
> 工具循环);0.6.0 撤了二分,**统一一条路径**,所以这就是 chat 的 agent 主循环
> 本身,改名 `AgentLoop`。跟 `AIAgent` / `run.py` 命名系列一致。

```python
class AgentLoop:
    def __init__(
        self,
        provider: BaseProvider,
        tools: dict[str, BaseTool],
        repo: ConvoRepo | None,
        convo_id: str | None,
        max_iter: int = _DEFAULT_MAX_TOOL_ITER,
    ) -> None: ...

    async def run(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        """agent 循环主流程。

        每轮:
          - 调 provider.generate(req) → 拿到 Claude 形态的 ChatEvent 流
          - forward events 给 caller
          - buffer assistant content blocks(从 content_block_start +
            content_block_delta+ + content_block_stop 累积)
          - message_stop 后:
              · 持久化 assistant content
              · 收上一轮 message_delta 里的 stop_reason 判断:
                  - stop_reason=tool_use → 跑工具 → yield tool_result events
                    → 拼下轮 req → 续
                  - 其它(end_turn / max_tokens / stop_sequence)→ yield
                    stream_done → break
        """
```

**关键不同(vs 0.5.0)**:
- **不再做 SSE 解析**:Provider 已经吐 typed events,AgentLoop 直接 `match event.kind`
- **不再合成 SSE 帧**:`tool_result` 直接是 `ChatEvent(kind="tool_result", ...)`,
  surface 自己负责序列化
- **错误传播显式化**:工具抛 → `ChatEvent(kind="tool_result", is_error=True, ...)`;
  Provider 抛 → `ChatEvent(kind="error", ...)` + break

### 6.3 max_iter 超限

`ChatEvent(kind="error", error_type="agent_iter_exceeded", error_message=...)`
+ break。Surface 层决定怎么呈现(CLI 红字 / Tauri toast / Gateway 自然语言提示)。

### 6.4 流式输出处理(Producer / AgentLoop / Surface 三段)

ChatEvent 流的完整链路:Provider 产 → AgentLoop 中转 → AIAgent 路由 → Surface
消费。每段职责正交,asyncio 协程的天然背压保证全链路无须显式流控。

#### 6.4.1 链路总览

```
Anthropic Messages API
  │ SSE bytes
  ▼
AnthropicProvider.generate()           ── SseParser 解析帧 → 1:1 翻译
  │ AsyncIterator[ChatEvent]
  ▼
AgentLoop.run()                         ── 转发 + 缓冲 + 注入 tool_result + 跨轮
  │ AsyncIterator[ChatEvent]
  ▼
AIAgent.run()                           ── 路由 + 锁包装
  │ AsyncIterator[ChatEvent]
  ▼
Surface(三选一)
  ├ CLI Renderer        (同进程,直接消费)
  ├ Sidecar JSON-RPC    (跨进程,序列化推送)
  └ Gateways (0.8.0)    (跨网络,平台节流)
```

#### 6.4.2 Producer 端约定(BaseProvider 子类)

`Provider.generate` 的流契约(沿用 0.1.0,跟"200 已发后断 TCP 不伪造事件"
精神一致):

- **200 前的错** → 抛 `ProviderError(code, message)`,由 AIAgent 捕获后转
  `ChatEvent(kind="error", error_type=code, error_message=...)` yield 给 Surface
- **200 后的错** → 直接 yield `ChatEvent(kind="error", ...)` + `return`,
  **不抛异常**(异常会污染 AsyncIterator 流契约)
- **不产 `stream_done`**:Provider 不管跨轮收敛,只产 Claude SSE 原生 8 种 event
- **背压自然形成**:Provider 用 `httpx.AsyncClient.stream()` + `aiter_bytes()`,
  消费方 `async for` 暂停 → 不读 → TCP 接收窗口满 → 上游 SSE 自动节流

```python
async def generate(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
    body = dataclasses.asdict(req)                          # ChatRequest 1:1 → Claude body
    async with self.http_client.stream("POST", "/v1/messages", json=body) as resp:
        if resp.status_code != 200:
            raise ProviderError(self._map_status(resp.status_code), ...)
        try:
            async for frame in SseParser.iter_frames(resp.aiter_bytes()):
                yield self._frame_to_event(frame)           # 1:1 翻 ChatEvent
        except (httpx.ReadError, httpx.ProtocolError) as e:
            yield ChatEvent(kind="error", error_type="upstream_stream_error",
                            error_message=str(e))
            return
```

#### 6.4.3 AgentLoop 中转规则

主控权在 AgentLoop:Provider 一次 `generate` 是**单轮**(message_start ~
message_stop),AgentLoop 决定要不要再来一轮。每轮职责:

1. **实时透传**:`async for event in provider.generate(...): yield event`
2. **内部累积**:从 `content_block_start` / `content_block_delta` /
   `content_block_stop` 重组 assistant content blocks,供持久化 + 拼下轮 req
3. **判 stop_reason**:从 `message_delta` 收 `stop_reason`
4. **message_stop 后决策**:
   - `tool_use` → 跑工具 → yield `tool_result` events(chariot 注入)→
     拼下轮 req → continue 续轮
   - `end_turn` / `max_tokens` / `stop_sequence` → yield `stream_done` → return
5. **超 max_iter** → yield `error(error_type="agent_iter_exceeded")`

```python
async def run(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
    current_req = req
    for _ in range(self.max_iter):
        assistant_blocks: list[dict] = []
        stop_reason: str | None = None
        async for event in self.provider.generate(current_req):
            yield event                                      # ① 实时透传
            self._buffer_block(assistant_blocks, event)      # ② 内部累积重组
            if event.kind == "message_delta":
                stop_reason = event.delta.get("stop_reason")
            if event.kind == "error":
                return                                       # ③ 错误终结
        # message_stop 之后
        await self._persist_assistant(current_req.convo_id, assistant_blocks)
        if stop_reason == "tool_use":
            tool_results = await self._execute_tools(assistant_blocks)
            for tr in tool_results:
                yield ChatEvent(kind="tool_result", tool_use_id=tr.id,
                                content=tr.content, is_error=tr.is_error)  # ④ 注入
            await self._persist_tool_results(...)
            current_req = self._build_next_req(current_req, assistant_blocks, tool_results)
            continue                                          # ⑤ 续轮
        else:
            yield ChatEvent(kind="stream_done")              # ⑥ 跨轮收敛
            return
    yield ChatEvent(kind="error", error_type="agent_iter_exceeded")
```

#### 6.4.4 Surface 端消费(三选一)

| Surface | 消费形态 | 序列化 |
|---------|---------|--------|
| **CLI Renderer** | `async for event in agent.run(req)` 直接 `match event.kind` dispatch | 内存 dataclass,无序列化 |
| **Sidecar JSON-RPC** | sidecar 的 `chat` method 内 `async for` 消费,每个 event 通过 `notify("chat_event", asdict(event))` 推 Tauri 前端 | `dataclasses.asdict()` → JSON 单行(newline-delimited) |
| **Gateways**(0.8.0) | 缓冲 + 节流(IM 平台限频);每 1~2s `bot.edit_message_text` 更新平台消息 | 平台特定富格式(ChatEvent 在内部消费,不暴露平台) |
| **ACP / MCP**(0.10.0+) | JSON-RPC notify,同 Sidecar | 同 Sidecar |

CLI 范例:

```python
async for event in agent.run(req):
    match event.kind:
        case "content_block_start" if event.content_block["type"] == "tool_use":
            renderer.show_tool_card(event.content_block["name"])
        case "content_block_delta" if event.delta["type"] == "text_delta":
            renderer.stream_token(event.delta["text"])
        case "content_block_delta" if event.delta["type"] == "input_json_delta":
            renderer.append_tool_input(event.index, event.delta["partial_json"])
        case "tool_result":
            renderer.show_tool_result(event.tool_use_id, event.content, event.is_error)
        case "message_stop":
            renderer.newline()
        case "error":
            renderer.show_error(event.error_type, event.error_message)
        case "stream_done":
            return
```

Sidecar 范例:

```python
@server.method("chat")
async def handle_chat(params: dict):
    req = ChatRequest(**params)
    async for event in agent.run(req):
        await server.notify("chat_event", dataclasses.asdict(event))
    return {"stream_id": req.convo_id, "ended_at": time.time()}
```

Gateway(0.8.0)范例 —— 缓冲 + 节流应对 IM 平台限频:

```python
async def handle_user_message(self, chat_id, text):
    msg_id = await self.bot.send_message(chat_id, "...")
    buf, last_flush = [], time.time()
    async for event in self.agent.run(req):
        if event.kind == "content_block_delta" and event.delta["type"] == "text_delta":
            buf.append(event.delta["text"])
            if time.time() - last_flush > 1.5:                    # 1.5s 节流
                await self.bot.edit_message_text(chat_id, msg_id, "".join(buf))
                last_flush = time.time()
        elif event.kind == "tool_result":
            await self.bot.send_message(chat_id, f"🔧 {event.content[:200]}")
    await self.bot.edit_message_text(chat_id, msg_id, "".join(buf))
```

#### 6.4.5 关键技术细节

| 问题 | 处理 |
|------|------|
| **背压** | asyncio 协程天然背压:Surface 消费慢 → `async for` 暂停 → AgentLoop 暂停 → Provider 暂停读 SSE → TCP 窗口满 → 上游自动节流。**无须特别代码** |
| **取消** | 用户 Ctrl+C / app 关闭 / 网络中断 → asyncio `CancelledError`。Provider 用 `try/finally` 关 httpx 连接;AgentLoop 在锁内 finalize 已写入的 message;持久化中断段标 `incomplete=True`,下次回放可识别 |
| **超时** | Provider httpx timeout(默认 30s,可配);AgentLoop 整体 timeout(可选,如 5 分钟超 → `error_type=agent_timeout`);Surface 各自 UI timeout |
| **错误等级** | `error_type` 是 fatal 类(`upstream_*` / `agent_iter_exceeded` / `agent_timeout`)→ 流终结;非 fatal 错误已在 `tool_result.is_error=True` 表达,不产 error event |
| **input_json_delta 拼装** | 流式给的是 JSON 切片(`'{"path":'` + `'"/tmp"'` + `'}'`)。AgentLoop 累积所有 `input_json_delta` 字符串,在 `content_block_stop` 时 `json.loads` 拿到完整 input;解析失败 → 注入 `tool_result(is_error=True, content="invalid_json: ...")` |
| **持久化时机** | `message_stop` 后整轮一次性持久化(assistant content + tool_result);**不在 token 级写库**(既保留实时流体验,又避免每个 token 都写库) |
| **多 surface 并发同 convo_id** | §7.2 双层锁保证串行;第二个并发请求等锁,asyncio 自然 backpressure |
| **buffer 内存上限** | 长 thinking / 大 JSON tool input 在 AgentLoop 内 buffer 时设上限(默认 10MB / block);超过 → 截断 + yield `error_type="block_too_large"` 终结流(防 OOM) |

#### 6.4.6 测试断言要点

每层流式契约用单测固化:

- `test_anthropic_provider.py`(§5.1):httpx MockTransport 喂 SSE bytes,断
  yield 出的 ChatEvent kind 序列形态;200 前 / 200 后错误处理分两个 case
- `test_loop.py`:mock provider 给多轮序列,断 AgentLoop 转发 + 注入
  tool_result + 跨轮收敛;max_iter 超抛 error event
- `test_run.py`:stateless / stateful / 路由 + 锁包装路径
- `test_chat_method.py`(sidecar):mock AIAgent 给 N 个 ChatEvent,断 stdout
  收到 N 条 `notify("chat_event")` + 1 条 `response`

详细见 FEATURE.md 各步「验收」段。

## 7. Conversation 层

### 7.1 表 schema

0.6.0 沿用 0.4.0 + 0.5.0 的五张表,但表名 / 列名做了 v5/v6/v7 的 rename + 一列
增量(详见 `chariot/database/models.py` 模块 docstring + `migrations/00[5-7]_*.sql`):

| migration | 改动 | 动机 |
|---|---|---|
| v5 (005) | `conversations` → `convos`,`messages.conversation_id` → `convo_id` | 跨层缩写统一(详 §7.1 后续段落) |
| v6 (006) | `models` → `providers`,`messages.model_name` → `provider_name`,`logs.model` → `provider` | 0.6.0 抽象层是 BaseProvider,DB 层跟上(`ChatRequest.model` / `options.model` 仍叫 model 对齐 Claude API) |
| v7 (007) | `providers` 加 `is_default INTEGER NOT NULL DEFAULT 0` | 默认 provider 机制(`chariot chat` 不传 `--provider` 走默认行;同时至多一行 = 1) |

**0.7.0 加 `skills` / `memory_facts`**(届时 migration v8/v9)。

#### `messages.content` 选 Claude 形态(决策依据)

存 Claude 形态 `list[ContentBlock]` 的 JSON 序列化(text / tool_use /
tool_result / image / thinking),跟 §3.1 `ChatRequest.messages` / §3.2
`ChatEvent` 三层一致。**不存 OpenAI 形态**(`tool_calls` 顶层 + `role=tool`
单独消息)。

| 维度 | Claude 形态(chariot 选) | OpenAI 形态(hermes 等) |
|---|---|---|
| `content` 字段 | `list[ContentBlock]`(平铺) | `str` 或多模态 block 数组 |
| 工具调用归属 | assistant message **内部** `tool_use` block | assistant message **顶层** `tool_calls` 数组 |
| 工具结果归属 | user message **内部** `tool_result` block | 单独 message,`role=tool` |
| system 提示词 | 不进 messages 表(在 `ChatRequest` / agent profile) | messages 第一条,`role=system` |

**对 0.7.0+ 自演化(Memory + Skills + evolve)的具体好处**:

1. **Skill trajectory 提取直接**:多轮 assistant `tool_use` + user `tool_result`
   在 message **内部** 成对出现,Python 一层 `for block in msg.content` 循环
   就能抽出工具使用轨迹。OpenAI 形态要靠 `tool_call_id` 跨 message 关联,
   逻辑复杂度高 30~50%
2. **错误模式分析直接**:`block.type == "tool_result" and block.is_error`
   一眼可读;OpenAI 形态要去 `role=tool` 消息内容里启发式判断
3. **`evolve()` 改 system 不动 messages**:system 在 `ChatRequest` 顶层字段 /
   agent profile,自演化修 system prompt 时 messages 表完全不动,历史会话
   回放安全。OpenAI 形态 system 是 `messages[0]`,修起来跟历史耦合
4. **extended thinking 落库可分析**:Claude 4 `thinking` block 直接落 content
   字段,evolve 找"为什么这次失败"有内省信息源。OpenAI o1/o3 的 reasoning
   不返客户端,无法落库
5. **工具调用统计直接**:`SELECT ... FROM messages, json_each(content) AS b
   WHERE json_extract(b.value, '$.type')='tool_use'` 平铺过滤;OpenAI 形态要
   跨 message 做 JOIN

**唯一代价**:JSON list 查询比纯字符串多一层(`json_each` / `json_extract`),
但 SQLite 3.38+ 原生支持。且实际自演化多半是"先 SELECT 整列 → Python
反序列化 → list comprehension"过滤,SQL 复杂度可忽略。

**跟 hermes 落库不互通**:跨项目消息迁移要写 schema 翻译脚本(预期代价,
不追求互通)。

**整套闭环**:**chariot 内核 IR(`ChatRequest` / `ChatEvent`)、落库形态
(`messages.content`)、上游协议(Anthropic Messages API)三层 1:1 一致**,
跨层不需要翻译;翻译只发生在非 Claude Provider 内部(0.7.0+ 的
`OpenAIProvider` / `LocalLlamaProvider` 等)。

### 7.2 跨进程并发(0.6.0 双层锁)

| 层 | 实现 | 范围 |
|---|---|---|
| **进程内** | `ConvoLockManager`(asyncio.Lock 字典) | 同进程内同 convo_id 串行 |
| **跨进程** | SQLite `BEGIN IMMEDIATE` 短事务包"load history + append" | 多进程同 convo_id 串行写 |

进程内锁解决"CLI 进程并发跑两次工具循环";跨进程锁解决"CLI + Gateway 并发写
同一 convo_id"。两层都需要。

> **vs 0.5.0**:0.5.0 只有进程内 asyncio.Lock(因为只有一个 server 进程)。
> 库化后多进程并发出现,必须加 SQLite 层。

### 7.3 失败处理

- 进程内锁超时(30s) → `ChatEvent(kind="error", error_type="convo_busy_local")`
- DB 锁等待超时(SQLite `busy_timeout`,默认 5s) → 自动重试 3 次;再失败 →
  `ChatEvent(kind="error", error_type="convo_busy_db")`

## 8. Surface 层

每个 surface 是一个 Python 模块 + 一个 entrypoint + 一个 wire codec。

### 8.1 CLI surface(`chariot/cli/`)

```
cli/
├── __main__.py
├── _runtime.py              ── installed_runtime(): AIAgent.from_db + dispose
├── commands/                ── 命令注册层
│   ├── chat.py              ── delegate 到 cli/repl.py / batch.py / once.py
│   ├── convo.py             ── 调 ConvoRepo(v5 起;原 conversation.py)
│   ├── logs.py              ── 调 LogRepo
│   ├── provider.py          ── 调 ProviderRepo + ProviderProber(v6 起;原 model.py)
│   ├── tool.py              ── 调 ToolRepo
│   ├── status.py            ── 显示 DB 路径 / 默认 provider / providers / tools / 版本
│   └── stats.py             ── logs 表统计(请求数 / 错误率)
├── context.py               ── ChatContext(持 AIAgent + 三种模式共享状态)
├── render.py                ── ChatEvent → 终端渲染(text / tool_use / tool_result)
├── repl.py                  ── 交互模式(prompt_toolkit)
├── batch.py                 ── 批量模式(`chariot chat --batch jobs.yaml`)
└── once.py                  ── 一次性模式(`chariot chat "hello"`)
```

**0.5.0 → 0.6.0 改动**:
- 撤 `chariot/sdk/` 整目录 + `chariot/cli/core/`(平铺到 cli/ 顶层)
- 撤 `chariot start` / `chariot stop` 命令(库化无 daemon)
- `chariot status` 改语义:从"问 daemon 状态"→"显示静态状态"
- `cli/commands/chat.py` 现持 `AIAgent` 实例,直接调 `agent.run(req)`
- Renderer 直接消费 `ChatEvent`(`content_block_delta(text_delta)` → stream_token,
  `content_block_stop` 对应 tool_use block → tool_use_line,`tool_result` →
  tool_result_line,`message_stop` → newline)

**S.7.1 加的 provider 切换 / 展示**:
- `chariot provider use <name>` —— 持久化默认 provider 到 DB(`is_default=1`);
  `chariot chat` 不传 `--provider` 时走默认行
- `chariot provider show [<name>]` —— 不带参数 = 当前默认;带参数 = 指定 entry
  详情(`api_key` 打码 `(set, len=N)` 不泄露内容,`api_key_env` 原值)
- `chariot provider list` 加 `default` 列(`*` 标记)
- `chariot chat --provider <name>` —— 本次会话覆盖默认(不动 DB);砍掉 0.5.0
  的 `--model` flag(语义跟 LLM model id 撞名 → 改名 `--provider`)
- REPL `/provider <name>` 仍是本地切换;新增 `/provider use <name>` 持久化
- **优先级**:CLI flag `--provider` > DB 默认(`is_default=1`)> die 提示

**S.7.3 加的 per-call options override**(`--model` / `--base-url` / `--api-key`):

- `chariot chat` 和 `chariot provider probe` 都接这三个 flag,临时覆盖 entry 的
  对应 options 字段。**不动 DB**;只在本次进程生效
- 实现走 `AIAgent.patch_provider_options(name, options_overrides=...)`:
  浅 merge `entry.options` 后用 `ProviderRegistry.build` 重建实例,替换
  `agent._providers[name]`。空 patch / 未知 name 静默 no-op
- 对 `provider probe`:走 `dataclasses.replace(entry, options=merged)` 后跑
  `ProviderProber.probe(temp_entry)`,不走 AIAgent 单例,不污染状态
- **优先级链**(详 §5.1):
  - `--model`:CLI flag → inline(无 env)
  - `--base-url`:CLI flag → inline → `ANTHROPIC_BASE_URL` env → 默认
  - `--api-key`:CLI flag → inline → `api_key_env` 指向的 env(默认 `ANTHROPIC_API_KEY`)
- 命名约定:**CLI flag 沿用 wire 字段名**(`--model` / `--base-url` / `--api-key`),
  跟 Anthropic SDK / 其它 LLM 工具的 CLI 习惯对齐;chariot IR 内部仍用
  `provider_name` 错开

### 8.2 Sidecar surface(`chariot/sidecar/`)

新增。Tauri 桌面壳的 Python sidecar(原计划名 `tui_gateway`,在 chariot 语境
"TUI" 字面误导,改名 `sidecar` —— 它就是 Tauri 的 sidecar 进程)。

```
sidecar/
├── __main__.py              ── asyncio 主循环 + JSON-RPC server
└── methods.py               ── 业务方法 dispatch
```

JSON-RPC 框架代码不在这——在 `chariot/rpc/jsonrpc.py`(共享给 acp / mcp)。

```text
Tauri 前端(TS)        chariot.sidecar(Python)
─────────────         ───────────────────────
                ─────► spawn child process
                       └─ stdio JSON-RPC bidirectional
─ rpc("chat", req) ───►
                       agent = AIAgent.from_db(...)
                       async for event in agent.run(req):
                           ◄─── notify("chat_event", event_dict)
─ rpc("list_conv") ───►
                       ◄─── return {conversations: [...]}
─ rpc("rename_conv", id, title) ─►
                       ◄─── return {ok: true}
```

**协议**:newline-delimited JSON over stdio。方法清单(0.6.0):
- `chat(req: ChatRequest)`:启动一次 chat run,server 通过 `chat_event` notify
  推送每个 `ChatEvent`,最后 return `{stream_id, ended_at}`
- `list_conversations()` / `get_conversation(id)` / `rename_conversation(id, title)`
  / `delete_conversation(id)`
- `list_tools()` / `enable_tool(name)` / `disable_tool(name)` / `config_tool(name, options)`
- `list_models()` / `add_model(...)` / `edit_model(...)` / `delete_model(...)` / `probe_model(name)`
- `list_logs(filters)`

**Tauri Rust 侧改动**:`packages/desktop/src-tauri/` 替换 sidecar 调用方式 ——
从"spawn server.exe + httpx 调 /v1/messages"改成"spawn chariot-sidecar.exe +
stdio JSON-RPC bidirectional"。Tauri 提供的 sidecar API 原生支持 stdin/stdout
双向流。

### 8.3 Gateways surface(0.8.0,`chariot/gateways/`)

`chariot gateway start --platform telegram`(对标 hermes `gateway/run.py`)。
长 live 进程,内置 `AIAgent`,接 Telegram bot poll → ChatRequest → AIAgent.run →
ChatEvent → 平台消息组装 → 发回。

```
gateways/                   ── 复数:装多个平台 adapter
├── __main__.py             ── chariot gateway start --platform <name>
├── base.py                 ── BaseGateway ABC
├── registry.py             ── GatewayRegistry(类比 ProviderRegistry)
└── builtin/                ── 跟 tools/ / providers/ 一致的抽象+实现分层
    ├── telegram.py         ── 0.8.0
    └── discord.py          ── 0.8.0
```

> **为什么有 `builtin/`**:跟 `tools/` `providers/` 同一套模式 —— 顶层契约 +
> 共享 utility,`builtin/` 装具体平台 adapter。0.11.0+ 加 `external/` 装第三方
> 平台插件(`bluebubbles` / `mattermost` / 自家 IM 等社区贡献项)。

0.6.0 不实现,只在路标里描述位置。

### 8.4 ACP surface(0.10.0+,`chariot/acp/`)

ACP(Agent Client Protocol,Zed 推出的标准):让 IDE / 编辑器(Zed / VSCode /
JetBrains)统一接 agent 后端,类似 LSP 之于 language server。

- chariot 是 ACP **server**(被 IDE 调用)
- 协议:stdio JSON-RPC(框架复用 `chariot/rpc/jsonrpc.py`)
- 方法:`agent/initialize` / `agent/chat` / `agent/cancel` / `agent/tool_use` 等
  ACP 标准方法

0.6.0 不实现,目录留位。

### 8.5 MCP surface(后续,`chariot/mcp/`)

MCP(Model Context Protocol,Anthropic 推出的标准):

- chariot 可以当 MCP **server**(把 chariot 工具暴露给别的 LLM 用)
- chariot 也可以当 MCP **client**(把别人的 MCP server 当工具源,扩充工具集)
- 协议:stdio JSON-RPC(框架复用 `chariot/rpc/jsonrpc.py`)

0.6.0 不实现,目录留位。

### 8.6 Tauri 前端(`packages/app/`)

**几乎不变**(组件 / Tailwind / shadcn 不动),仅替换数据访问层:
- 撤 `lib/streams.ts` 里 SSE 解析(原本解 `/v1/messages` 流)
- 改为接收 Tauri `event.listen("chat_event", ...)` 推来的 typed `ChatEvent`
- `lib/api.ts` 里 `fetch("/admin/...")` 全改为 `invoke("rpc", { method, params })`
  (Tauri IPC 包到 sidecar)

## 9. RPC 框架(`chariot/rpc/`)

stdio JSON-RPC 实现集中在这,sidecar / acp / mcp 三个 surface 共享。

```
rpc/
├── __init__.py
└── jsonrpc.py               ── 0.6.0:reader / writer / 帧协议 / dispatcher / 类型
                                  在同一文件(200-300 行)
```

**未来扩展位置**(0.10.0+ ACP / MCP 立项后视情况拆):
- `rpc/types.py`(Request / Response / Error / Notification 拆出)
- `rpc/errors.py`(JSON-RPC 标准错误码常量,如 -32700 ParseError)
- `rpc/dispatcher.py`(通用 method dispatch)
- `rpc/transport/`(若 MCP 走 WebSocket / HTTP 加 transport 抽象)

## 10. 撤掉的东西

| 模块 / 文件 | 命运 | 替代 |
|---|---|---|
| `chariot/server/app.py` | **撤** | 无;不再有 fastapi app |
| `chariot/server/__main__.py` | **撤** | 无;不再有 server 进程 |
| `chariot/server/controller/dataplane.py`(`/v1/messages`) | **撤** | AIAgent.run 直接产 ChatEvent |
| `chariot/server/controller/{conversations,models,tools,logs,stats,runtime}.py` | **撤** | CRUD 逻辑迁到 `chariot/repos/` 方法,被 CLI / sidecar 直接调 |
| `chariot/server/controller/errors.py` | **迁 + 改名** | `chariot/agent/exceptions.py`(去掉 fastapi 依赖) |
| `chariot/server/runtime/{endpoint,lockfile,watcher}.py` | **撤** | 库化无 daemon,无单实例锁 |
| `chariot/server/service/log_writer.py` | **迁** | `chariot/repos/log_writer.py` |
| `chariot/server/service/model_prober.py` | **迁 + 改名** | `chariot/providers/prober.py` |
| `chariot/server/service/exceptions.py` | **迁 + 改名** | `chariot/agent/exceptions.py` |
| `chariot/sdk/{client,chat,streams,discover}.py` | **撤** | CLI 直接 import `chariot.agent` 等模块;外部 Python 用户也直接 import |
| `chariot/shared/sse.py` | **迁** | `chariot/providers/_sse.py`(下划线 = 包内私用) |
| `chariot/cli/core/` 整目录 | **撤(平铺)** | 5 文件移到 `chariot/cli/` 顶层 |
| `chariot start` / `chariot stop` 命令 | **撤** | 库化无 daemon |
| `scripts/build.py --target server` | **改** | sidecar 目标改为 `chariot.sidecar` |
| `chariot-server.spec` | **改名** | `chariot-sidecar.spec` |

## 11. 迁移流程(库化 + 重组)

`chariot/server/` → 各功能模块顶层散布:

| 0.5.0 路径 | 0.6.0 路径 | 备注 |
|---|---|---|
| `server/agent.py`(`Agent` 类) | `agent/run.py`(`AIAgent` 类) | 重写 + 改名(类 + 文件) |
| `server/agent.py` 的 `_stream_tool_loop_*` 等 | `agent/loop.py`(`AgentLoop` 类) | 拆出 + 重写 |
| `server/convo_lock.py` | `agent/convo_lock.py` | 不动 |
| `server/config.py` | `agent/config.py` | 删 server 专属字段(host/port 等) |
| `server/database/` | `database/`(顶层) | 不动 |
| `server/repository/log.py` | `repos/log_repo.py` | 改名(跟兄弟统一) |
| `server/repository/{conversation,model,tool}_repo.py` | `repos/{conversation,model,tool}_repo.py` | 不动 |
| `server/service/log_writer.py` | `repos/log_writer.py` | 迁位 |
| `server/service/model_prober.py` | `providers/prober.py` | 迁位 + 改名 |
| `server/model/` | `providers/` | **重写**(Model → Provider,新接口) |
| `server/model/{base,registry}.py` | `providers/{base,registry}.py` | 重写 |
| `server/model/mock.py` | `providers/builtin/mock.py` | 重写 + 移到 builtin/ |
| `server/model/anthropic.py` | `providers/builtin/anthropic.py` | 重写 + 移到 builtin/ |
| `server/tool/` | `tools/` | base / registry 不动 |
| `server/tool/{base,registry}.py` | `tools/{base,registry}.py` | 不动 |
| `server/tool/{readfile,listdir,shellexec,httpget}.py` | `tools/builtin/{read_file,list_dir,shell_exec,http_get}.py` | 改下划线命名 + 移 builtin/ |
| `shared/sse.py` | `providers/_sse.py` | 迁位 |
| `cli/core/{context,render,repl,batch,once}.py` | `cli/{context,render,repl,batch,once}.py` | 平铺到 cli/ 顶层 |
| 新增 | `sidecar/` | Tauri sidecar(原计划 tui_gateway) |
| 新增 | `rpc/jsonrpc.py` | 共享 RPC 框架 |

`chariot/cli/` 内部:
- `cli/core/proxy_client.py`(若存在) → 撤
- `cli/core/context.py` → 移到 `cli/context.py`,内部改持 `AIAgent`
- `cli/commands/{model,tool,conversation,...}.py` → 直接调 repo
- `cli/commands/{start,stop}.py` → 撤

## 12. 单例 / lifespan

0.5.0 的 `Agent._current` ClassVar 单例 + `install_from_config` 改:
- **保留单例语义**,但每个进程一个实例(不再有"全局 chariot 进程")
- `AIAgent.from_db(db_path)` classmethod = 装载 + 设单例 + 返实例
- 进程退出时 sqlalchemy session / httpx client 各自关闭(沿用 0.5.0 lifespan)

## 13. 依赖移除 / 保留

撤:
- `fastapi`(server / controller / dataplane 全删)
- `uvicorn`(server 入口)

保留:
- `httpx`(Provider 调上游)
- `sqlalchemy[asyncio]` + `aiosqlite`(DB)
- `typer`(CLI)
- `ulid-py`(消息 / 会话 ID)
- `prompt_toolkit`(REPL)

新增:
- 无(stdio JSON-RPC 不需要库,标准 `json` + `asyncio.streams` 即可)

## 14. 测试覆盖

测试目录镜像源码新结构:

```
tests/
├── agent/
│   ├── test_run.py        ── AIAgent.run e2e
│   ├── test_loop.py       ── AgentLoop 单元
│   ├── test_chat_request.py
│   ├── test_chat_event.py
│   ├── test_exceptions.py
│   └── test_convo_lock.py
├── repos/                       ── 各 *_repo.py + log_writer.py 测试
├── tools/
│   ├── test_base.py / test_registry.py
│   └── builtin/                 ── 4 内置工具单测
├── providers/
│   ├── test_base.py / test_registry.py / test_prober.py
│   └── builtin/
│       ├── test_mock.py
│       └── test_anthropic.py    ── httpx MockTransport 喂 SSE
├── rpc/
│   └── test_jsonrpc.py          ── stdio JSON-RPC 帧测 + dispatch
├── cli/
│   ├── test_repl.py             ── 直接构造 AIAgent + mock provider
│   ├── test_chat_command.py     ── commands/chat.py
│   └── test_*_command.py
└── sidecar/
    └── test_methods.py          ── e2e:mock AIAgent + 断 notify 序列
```

**撤**:
- `tests/server/`(整个目录)
- `tests/sdk/`(整个目录)

## 15. 设计模式

- **封装与内聚优先**(CLAUDE.md ⭐):AgentLoop / AIAgent / Provider 都是类承载状态,
  模块级零自由函数;`ChatRequest` / `ChatEvent` 是 frozen dataclass(不可变值类型)
- **抽象 / 实现分层**(0.6.0 重组核心):`tools/` `providers/` `gateways/` 顶层
  装契约 + 共享 utility,`builtin/` 子目录装具体实现(项目自带)。0.11.0+ 加
  `external/` 子目录装第三方插件 / 用户安装的实现。**所有"装多个同类可插拔实现"
  的目录都套这套**;`acp/` `mcp/` 是单一协议实现,不套
- **Adapter 模式**:Provider 是 LLM API 后端的 Adapter;每个 surface 内部的"event
  → wire format"也是 Adapter(CLI Renderer / Sidecar Encoder / Gateway 平台
  Encoder / ACP MethodHandler / MCP MethodHandler)
- **dispatch 字典**:ChatEvent 在消费侧用 `match event.kind`(Python 3.10+ 内置
  dispatch),无 isinstance 树
- **依赖注入**:`AIAgent.__init__` 接 `repo` / `lock_manager` 参数,测试可注 mock
- **共享 wire 框架**:sidecar / acp / mcp 都是 stdio JSON-RPC,框架代码集中
  `chariot/rpc/jsonrpc.py`,各自只写业务 dispatch

## 16. 0.7.0+ 路标

| 版本 | 主题 | 主要新增 |
|---|---|---|
| **0.7.0** | OpenAIProvider + Memory + Skills | `providers/builtin/openai.py` / `skills` 表 + `BaseSkill` ABC / `memory_facts` 表 + 提取规则 / AIAgent 在每轮后期触发 skill 创建提议 |
| **0.8.0** | Gateways(Telegram + Discord) + 自我进化循环 | `chariot/gateways/` + 平台 Adapter / `agent.evolve()` 定期任务 |
| **0.9.0** | Cron 调度 + 多 AIAgent 实例 + LocalLlamaProvider | `cron_jobs` 表 / Scheduler / `agent_id` 维度全表加 / `providers/builtin/local_llama.py` |
| **0.10.0+** | TUI 升级 / ACP / MCP / 剩余 Gateway 平台 | `chariot/acp/` / `chariot/mcp/` 落地;Subagent 派生 |
| **0.11.0+** | Plugins 系统 | `tools/external/` / `providers/external/` |

(批量 / RL 跳过;`f.daytona/modal/singularity` 砍;`a.小众平台`砍 —— 沿用此前路线决议。)

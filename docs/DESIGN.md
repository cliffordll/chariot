# Chariot 架构设计(0.2.0)

> **当前版本**:`0.2.0`(开发中)
> **上一版归档**:[`docs/history/0.1.0/DESIGN.md`](history/0.1.0/DESIGN.md)
>
> **0.2.0 关键变更**(vs 0.1.0):
> - 对外端点从三个收窄到一个(`/v1/messages`);OpenAI 兼容剥离给外部转换器(LiteLLM 等)
> - Model 协议契约从"必须支持三协议 schema"简化为"只支持 Anthropic Messages"
> - 引入配置 + 注册中心,支持运行时切换真实模型(AnthropicModel 是首个真模型)

---

## 1. 一句话总结

**Chariot = 本机 HTTP server + Anthropic Messages 协议 + 可热插拔 Model**

- 对外只接 `POST /v1/messages`(Anthropic 原生)
- 内部 `Model` 实现按配置注入(默认 Mock,真实模型如 `AnthropicModel`)
- OpenAI 客户端通过外部转换器(LiteLLM / claude-code-router 等)接入,chariot 不做协议翻译

---

## 2. 分层

```
客户端 (Anthropic SDK / claude code / 自定义)
        │  POST /v1/messages
        ▼
Controller (controller/dataplane.py)
   读 body → Agent.handle(body)
        │
        ▼
Agent (server/agent.py)
   - 类级单例(install / current / uninstall)
   - 探测 stream → model.respond → 记日志
   - 0.3.0+ 这层加多轮记忆 / 工具调用 / 进化循环
        │
        ▼
Model Protocol (server/model/base.py)
   async respond(body, *, stream) -> Response
   无状态 / 不碰 DB / 不记 log
        │
        ├── MockModel        (默认 fallback)
        ├── AnthropicModel   (透传到 Anthropic API)
        └── (未来) LocalLlamaModel ...

Lifespan startup:
   ConfigLoader.load() → ChariotConfig
                         ↓
   ModelRegistry.build(active_entry) → Model
                         ↓
   Agent.install(model=...)
```

---

## 3. 唯一对外端点

| 端点 | 协议 | 请求 schema | 响应 schema |
|---|---|---|---|
| `POST /v1/messages` | Anthropic Messages | `{model, max_tokens, messages, stream?, system?, ...}` | `{id, type:"message", role:"assistant", content:[...], stop_reason, usage}` |

**OpenAI 客户端怎么接**:推荐用 [LiteLLM](https://github.com/BerriAI/litellm) 等成熟代理,把 chariot 配成 Anthropic 后端即可。chariot 不内置该转换层 —— 专业的事交给专业项目,chariot 的差异化在 Agent 层(0.3.0+ 的记忆 / 工具 / 进化循环)。

SSE 事件序列(沿用 0.1.0):
`message_start` → `content_block_start` → N × `content_block_delta` → `content_block_stop` → `message_delta` → `message_stop`

---

## 4. 数据面流程

```
client POST /v1/messages { model, stream, messages }
         │
         ▼
controller.messages(request):
    body = await request.body()
    return await Agent.current().handle(body)
         │
         ▼
Agent.handle(body):
    t0 = monotonic()
    model_hint = _detect_model_hint(body)     # 仅作日志
    is_stream  = _detect_stream(body)
    try:
        resp = await self.model.respond(body, stream=is_stream)
        await log_writer.record(status="ok", model=model_hint, latency_ms=...)
        return resp
    except ServiceError as e:
        await log_writer.record(status="error", error=f"{e.code}: {e.message}", ...)
        raise
         │
         ▼
AnthropicModel.respond(body, *, stream=True):
    payload = self._rewrite_model_id(body)    # client.model → 配置 model_id
    return await self._stream(payload)        # httpx → upstream /v1/messages,SSE 透传
```

响应一路透传回 client;Controller / Agent 不改 body,只追加 log。

---

## 5. Model 接口契约

```python
class Model(Protocol):
    name: str  # 写入 logs.model;UI 展示
    async def respond(
        self,
        body: bytes,
        *,
        stream: bool,
    ) -> Response: ...
```

三条硬约束(同 0.1.0,只删除 protocol 维度):
1. **无状态**。多轮对话由客户端在 body.messages 里管理
2. **不碰 DB**。日志归 Agent
3. **不感知"上游"**。Model 是响应的源头(直接生成 / 透传 / 调本机 llama 都算)

新增 builder 契约(支持 ModelRegistry 自动构造):

```python
@classmethod
def from_config(cls, options: dict[str, Any]) -> Self: ...
```

每个 Model 类用 `@ModelRegistry.register("type_name")` 装饰器注册,模块 import 即生效。

---

## 6. 配置层

### 6.1 文件位置

`~/.chariot/config.toml`(env `CHARIOT_CONFIG` 可覆盖)。无文件 → `ChariotConfig.empty()` → fallback `MockModel`。

### 6.2 Schema

```toml
[[models]]
name = "claude-opus"
type = "anthropic"
[models.options]
api_key_env = "ANTHROPIC_API_KEY"
base_url    = "https://api.anthropic.com"
model_id    = "claude-opus-4-5"

[[models]]
name = "local-llama"
type = "llama_local"
[models.options]
endpoint = "http://127.0.0.1:11434"
model_id = "llama3.1-70b"

[active]
model = "claude-opus"
```

### 6.3 类设计

| 类 | 文件 | 职责 |
|---|---|---|
| `ModelEntry` | `chariot/server/config.py` | 单个 model 条目(name / type / options dict) |
| `ChariotConfig` | `chariot/server/config.py` | 顶层配置(models 列表 + active);`empty()` / `active_entry()` / `from_dict()` |
| `ConfigLoader` | `chariot/server/config.py` | `load(path=None) -> ChariotConfig`;`tomllib` 解析;DEFAULT_PATH = `~/.chariot/config.toml` |
| `ModelRegistry` | `chariot/server/model/registry.py` | `@register("type")` 装饰器收集 builder;`build(entry)` 派发;`known_types()` |

封装内聚要点:

- `chariot/server/config.py` 只对外暴露 `ConfigLoader.load()` 和 `ChariotConfig` 数据类;`ModelEntry` / 解析细节都内聚在文件内
- `ModelRegistry` 用 ClassVar 挂注册表,classmethod 管理生命周期;模块级零可变变量

---

## 7. logs 表(沿用 0.1.0,schema 不变)

```sql
CREATE TABLE logs (
    id            TEXT PRIMARY KEY,
    model         TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    latency_ms    INTEGER,
    status        TEXT NOT NULL,
    error         TEXT,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_logs_created_at ON logs(created_at);
```

Agent 仍是唯一日志写入者;0.2.0 不动 schema。

---

## 8. 管理面

```
GET  /admin/ping       → {ok: true}
GET  /admin/status     → {version, uptime_ms, model, url}
POST /admin/shutdown   → graceful shutdown
GET  /admin/logs       → list LogOut(limit / offset / since)
GET  /admin/stats      → {period, total_requests, success_rate, avg_latency_ms}
GET  /admin/models     → 新增:列出可选 model + 当前 active + 可用 type
POST /admin/models     → 新增:切换 active(body: {name})
```

`/admin/models` 行为:

- GET → `{available: ["claude-opus", "local-llama"], active: "claude-opus", types: ["mock", "anthropic", "llama_local"]}`
- POST `{name}` → 用对应 entry rebuild Model → `Agent.install(model=...)` 覆盖 → 返回新 active

---

## 9. 单例 / lifespan

```python
async with lifespan(_app):
    await init_db()
    config = ConfigLoader.load()
    Agent.install_from_config(config)        # 无 config 走 mock,有 config 走 registry
    yield
    Agent.uninstall()
    await dispose_db()
```

```python
# Agent 类新增
@classmethod
def install_from_config(cls, config: ChariotConfig) -> Agent:
    entry = config.active_entry()
    model = ModelRegistry.build(entry) if entry else mock_model
    return cls.install(model=model)
```

---

## 10. AnthropicModel 实现要点

```python
@ModelRegistry.register("anthropic")
class AnthropicModel:
    name = "anthropic"

    def __init__(self, *, api_key: str, base_url: str, model_id: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            timeout=httpx.Timeout(connect=10, read=300, write=30, pool=10),
        )
        self._model_id = model_id

    @classmethod
    def from_config(cls, options: dict[str, Any]) -> AnthropicModel:
        env_name = options.get("api_key_env", "ANTHROPIC_API_KEY")
        api_key = os.environ.get(env_name)
        if not api_key:
            raise ConfigError(f"环境变量 {env_name} 未设置")
        return cls(
            api_key=api_key,
            base_url=options.get("base_url", "https://api.anthropic.com"),
            model_id=options["model_id"],
        )

    async def respond(self, body: bytes, *, stream: bool) -> Response:
        payload = self._rewrite_model_id(body)
        return await (self._stream(payload) if stream else self._unary(payload))
```

错误映射:

- 上游 401 / 403 → 502(用户配置错误,但本服务对上游不可用)
- 上游 429 → 透传 429
- 上游 5xx → 502
- 网络超时 / 连接拒绝 → 502 + error 文案带原因
- **流式中途断开**:沿用 0.1.0 "200 已发后只能断 TCP,不伪造事件"

---

## 11. 设计模式

| 模式 | 用在哪 | 解决什么 |
|---|---|---|
| **Strategy** | `Model` Protocol 多实现 | Agent 持有一个引用,运行时可换 model |
| **Registry + Factory Method** | `ModelRegistry` `@register` + `Model.from_config` | 加新 model 不改 Agent / Controller / lifespan;只写新文件 + 一行装饰器 |
| **Singleton(类级)** | `Agent._current: ClassVar` + `install / current / uninstall` | 模块级零可变变量,符合封装顶级原则 |

刻意**不**用的:

- ❌ Adapter — 单协议下没"三分支 dispatch",造一个 Adapter 反违反"内聚优先于 DRY"
- ❌ Builder — 配置直接 dataclass + classmethod 构造,不需分步
- ❌ Chain of Responsibility — 没"链式 fallback"需求

---

## 12. 测试覆盖

| 文件 | 覆盖 |
|---|---|
| `tests/server/test_config.py` | ConfigLoader 解析合法 / 非法 / 缺失 / env 覆盖 |
| `tests/server/test_model_registry.py` | register / build / unknown type / known_types |
| `tests/server/test_model_mock.py` | MockModel(单协议化后)非流 + SSE + 错误路径 |
| `tests/server/test_model_anthropic.py` | 用 `respx` 模拟上游;非流 / 流 / 错误码映射 / model_id 改写 |
| `tests/server/test_agent.py` | handle 转发契约 + 日志 + factory + install_from_config |
| `tests/server/test_dataplane.py` | 单端点 → Agent + 默认 Mock 端到端 |
| `tests/server/test_admin.py` | /admin/ping、/admin/status、/admin/logs、/admin/models |
| `tests/sdk/test_client_admin.py` | ProxyClient admin + models 切换 |
| `tests/cli/test_commands.py` | CLI 子命令注册 + help + `chariot model list / use` |

---

## 13. 0.3.0+ 路标

详见 `docs/ROADMAP.md`。要点:

- **0.3.0**:Agent 层多轮记忆 + 工具调用
- **0.4.0**:进化循环(读 logs feedback 调权重 / 切 model / 修 prompt)
- **0.5.0**:多 Agent 实例(logs 加 agent_id)

这些都不动 Controller / Model 接口,只在 Agent 层加。

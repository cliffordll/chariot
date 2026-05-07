# Chariot 架构设计(0.6.0)

> **当前版本**:`0.6.0`(开发中)
> **上一版归档**:[`docs/history/0.5.0/DESIGN.md`](history/0.5.0/DESIGN.md)
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
> **不变的事**:BaseTool / ToolRegistry / 4 内置工具、ConversationRepo /
> ConversationLockManager 单进程内的 per-conv 串行(库化后转为"per-conv +
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
- **同进程内 per-conv 串行**:`ConversationLockManager`(asyncio.Lock 字典)保
  单进程内同一 conversation_id 的 agent loop 不会被并发请求穿插
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

**设计依据**:跟 Claude Messages API 的 request body schema **1:1 平铺对应**。
理由跟 ChatEvent 一致——降低学习成本,AnthropicProvider 实现极简(几乎是
`dataclasses.asdict(req) → httpx.post(json=...)`)。Claude API 用户能直接
`ChatRequest(**claude_body)` 把现有 Claude 调用代码搬过来。

```python
@dataclass(frozen=True)
class ChatRequest:
    # ─── Claude Messages API 字段(顺序按官方 spec) ───
    model: str                                    # entry name(chariot 内部当 Provider 路由)
    messages: list[Message]
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
    conversation_id: str | None = None            # None = stateless;ULID = stateful
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

**关于 `model` 字段语义**:Claude API 里 `model` 是 LLM 模型 ID(如
`claude-sonnet-4-6`);chariot 这里 `model` 是 entry name(用户在 `models` 表
里的命名,如 `claude` / `mock` / `gpt-4`),由 AIAgent 根据 entry name 路由到
对应 Provider 实例,Provider 内部把真实的 model ID 传给上游 API。
**字段名跟 Claude 一致,语义在 chariot 层做了一层抽象**(更适合多 Provider 场景)。

**不引入的 Claude 字段**:
- `stream`:chariot 内核固定流式(`Provider.generate` 总是 `AsyncIterator`),
  非流式由 surface 自己聚合;不暴露给用户
- `service_tier`:Anthropic 计费层级,chariot 不暴露(用户在 Provider options
  里配)
- `anthropic_version` / `anthropic_beta`:HTTP header 级,`AnthropicProvider`
  内部处理,不进 ChatRequest

**chariot 扩展的两个字段**(放最后,跟 Claude 字段不冲突):
- `conversation_id`:0.4.0 起 stateful 多轮触发(0.6.0 起从
  `X-Chariot-Conversation` header 升级到顶层字段)
- `agent_id`:0.9.0+ 多 AIAgent 实例路由;0.6.0 默认 `None`,字段先占位

**OpenAIProvider 怎么对接 ChatRequest**(0.7.0,作为非 Claude Provider 的范例):
- `model` → OpenAI 也叫 `model`,直接传
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

        - stateful(req.conversation_id 非空):内部 load history + persist
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
              │  ─ conversation_lock.py                 │
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

## 5. Provider 接口契约

`chariot/providers/base.py`:

```python
@dataclass(frozen=True)
class BaseProviderConfig:
    """Provider 通用配置(子类可继承加专属字段)。"""
    name: str           # entry name(用户写的,如 "claude" / "mock")
    model: str          # 上游真实 model(给 LLM API)

class BaseProvider(ABC):
    """Provider 抽象基类。具体实现见 providers/builtin/。"""
    config: BaseProviderConfig

    @classmethod
    @abstractmethod
    def from_options(cls, options: dict[str, Any]) -> Self:
        """从 ModelEntry.options 构造;options 不合法 raise ConfigError。"""

    @abstractmethod
    async def generate(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        """跑一次 LLM 请求,产 ChatEvent 流(message_start / content_block_* /
        message_delta / message_stop / error / ping)—— 即 Claude SSE 形态。

        子类不知道工具循环、不知道 Conversation、不写 DB —— 纯输入输出。
        OpenAIProvider / LocalLlamaProvider 等也产同一份形态,内部翻译。
        """
```

**命名约定**:抽象基类用 `Base*` 前缀(`BaseProvider` / `BaseTool` / `BaseGateway`
/ `BaseSkill`),具体子类不带前缀(`AnthropicProvider` / `ReadFileTool` 等)。
文件名沿用 `base.py` 惯例,文件里装该层的 base class。

**vs 0.5.0 `Model.respond(body: bytes, *, stream: bool) -> Response`**:
- 输入从原始 JSON 字节 → 解构后的 `ChatRequest`(类型安全)
- 输出从 fastapi `Response`(可能 stream / 可能 unary)→ 统一 `AsyncIterator[ChatEvent]`(协议无关)
- 错误处理:子类内部 raise `ProviderError`(`upstream_auth_failed` / `upstream_unreachable` / `upstream_server_error` / `rate_limited`),AIAgent 包成 `ChatEvent(kind="error")` 给 surface

### 5.1 `AnthropicProvider`(`chariot/providers/builtin/anthropic.py`)

- 用 httpx 调上游 `/v1/messages`,**body 构造而非透传**(从 `ChatRequest` 拼请求)
- 流式:消费上游 SSE 字节,解析成 `ChatEvent` yield。**chariot 不再做"字节级透传"**,
  解析后再发(代价:多一次反序列化;收益:协议解耦)
- SSE 解析复用 `chariot/providers/_sse.py`(共享 utility,后续 OpenAIProvider 也用)
- 错误码映射沿用 0.5.0:401/403 → upstream_auth_failed,5xx → upstream_server_error

### 5.2 `MockProvider`(`chariot/providers/builtin/mock.py`)

- 不走任何 HTTP / SSE,直接 `yield` Claude 形态的 ChatEvent 序列:
  `message_start` → `content_block_start(text)` → `content_block_delta(text_delta)+`
  → `content_block_stop` → `message_delta(stop_reason=end_turn)` → `message_stop`
  → `stream_done`
- 0.5.0 的"手工拼 Anthropic SSE"代码完全删掉,但事件形态不变(从拼字节升级
  为产 typed dataclass)

### 5.3 `OpenAIProvider`(0.7.0,`chariot/providers/builtin/openai.py`)

- OpenAI Chat Completions / Responses 兼容(覆盖 OpenRouter / Kimi / DeepSeek /
  z.ai / Xiaomi / NVIDIA NIM 等)
- Provider 间共性(httpx client / timeout / 错误码映射)抽到 `_HttpProviderBase`
  父类共享(放 `providers/base.py` 同文件或 `providers/_http_base.py`)

### 5.4 `ProviderProber`(`chariot/providers/prober.py`)

- 共享 utility:对任意 Provider 实例做 ping(发个最小 chat 请求验通)
- 给 `chariot model probe <name>` CLI 用

## 6. AIAgent 内核(`chariot/agent/run.py` + `loop.py`)

### 6.1 `AIAgent` 类(`chariot/agent/run.py`)

```python
class AIAgent:
    def __init__(
        self,
        *,
        providers: dict[str, BaseProvider],   # entry_name → BaseProvider 子类
        tools: dict[str, BaseTool],           # tool_name → BaseTool 子类
        repo: ConversationRepo,           # 持久化层(注入,测试可 mock)
        lock_manager: ConversationLockManager,
    ) -> None: ...

    @classmethod
    def from_db(cls, db_path: Path) -> Self:
        """从 ~/.chariot/chariot.db 装载 ModelEntry / ToolEntry,
        构造所有 Provider / Tool 实例,返就绪 AIAgent。

        每个 surface 进程启动时调一次。"""

    async def run(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        """主入口。"""
```

`AIAgent.run` 实现拆成两条路径(取消 0.5.0 fast/slow path 的二分,统一走一条):

```text
AIAgent.run(req):
    if req.conversation_id:
        async with lock_manager.acquire(req.conversation_id):
            yield from _run_with_conv(req)
    else:
        yield from _run_stateless(req)

_run_with_conv / _run_stateless 都委托给 AgentLoop:
    loop = AgentLoop(provider, tools, repo, conversation_id)
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
        repo: ConversationRepo | None,
        conversation_id: str | None,
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
        await self._persist_assistant(current_req.conversation_id, assistant_blocks)
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
    return {"stream_id": req.conversation_id, "ended_at": time.time()}
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
| **多 surface 并发同 conv_id** | §7.2 双层锁保证串行;第二个并发请求等锁,asyncio 自然 backpressure |
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

`conversations` / `messages` / `tools` / `models` / `logs` 不变(沿用 0.4.0 + 0.5.0),
0.6.0 不加表。**0.7.0 加 `skills` / `memory_facts`**(届时 migration v6/v7)。

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
| **进程内** | `ConversationLockManager`(asyncio.Lock 字典) | 同进程内同 conv_id 串行 |
| **跨进程** | SQLite `BEGIN IMMEDIATE` 短事务包"load history + append" | 多进程同 conv_id 串行写 |

进程内锁解决"CLI 进程并发跑两次工具循环";跨进程锁解决"CLI + Gateway 并发写
同一 conv_id"。两层都需要。

> **vs 0.5.0**:0.5.0 只有进程内 asyncio.Lock(因为只有一个 server 进程)。
> 库化后多进程并发出现,必须加 SQLite 层。

### 7.3 失败处理

- 进程内锁超时(30s) → `ChatEvent(kind="error", error_type="conversation_busy_local")`
- DB 锁等待超时(SQLite `busy_timeout`,默认 5s) → 自动重试 3 次;再失败 →
  `ChatEvent(kind="error", error_type="conversation_busy_db")`

## 8. Surface 层

每个 surface 是一个 Python 模块 + 一个 entrypoint + 一个 wire codec。

### 8.1 CLI surface(`chariot/cli/`)

```
cli/
├── __main__.py
├── commands/                ── 命令注册层
│   ├── chat.py              ── delegate 到 cli/repl.py / batch.py / once.py
│   ├── conversation.py      ── 调 ConversationRepo
│   ├── logs.py              ── 调 LogRepo
│   ├── model.py             ── 调 ModelRepo + ProviderProber
│   ├── tool.py              ── 调 ToolRepo
│   ├── status.py            ── 显示 DB 路径 / provider 数 / tool 数 / 版本
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
| `server/conversation_lock.py` | `agent/conversation_lock.py` | 不动 |
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
│   └── test_conversation_lock.py
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

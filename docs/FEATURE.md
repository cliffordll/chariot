# Chariot 0.6.x 推进表

> **当前活跃**:`0.6.5`(基于 0.6.0 的架构修正增量;0.6.0 主线步骤未变)
> **上一版归档**:[`docs/history/0.5.0/FEATURE.md`](history/0.5.0/FEATURE.md)
>
> **0.6.5 主题**:对照 hermes-agent 修正 0.6.0 长跑场景架构盲区。Provider 不再持
> httpx client(升 `ClientCache` 进程级共享)+ AIAgent 撤单例(改 `AgentRegistry`
> per-session)+ `ChatRequest.model` per-call 字段加回。Surface / 工具 / DB
> schema 全部不变。详 [本文 0.6.5 patch 列表](#065-patch-列表2026-05-增量) +
> [`DESIGN.md`](DESIGN.md) §5 整章。
>
> **0.6.0 主题**:**架构定位扭转 + 目录全面重组 + Claude 形态 IR**。`server/`
> 退役;顶层目录按职责重组(`agent/` 缩成狭义内核,`tools/` / `providers/` /
> `repos/` / `database/` / `rpc/` 平铺顶层为功能模块,`cli/` / `sidecar/` 是
> surface);**`ChatRequest` / `ChatEvent` / `messages` 落库 三层都跟 Claude API
> 1:1 对齐**。详细架构见 [`DESIGN.md`](DESIGN.md)。

每步推进规则(沿用):每完成一步 → 跑「验收」全条目 → 等用户确认"通过"再标 ✅,
然后 commit。一个 FEATURE 步骤 = 一个 commit。

**步骤组织**:
- **S.1 ~ S.3**:核心类型 + Provider 抽象(纯增量,不破坏现有 server)
- **S.4 ~ S.6**:Tool / Repo / Database / Lock 迁入新结构,AgentLoop + AIAgent 落地
- **S.7**:CLI 切到直接 import core(撤 SDK / 撤 cli/core/ / 撤 daemon 命令)
- **S.8 ~ S.10**:rpc/jsonrpc.py + sidecar 新建 + Tauri 切 stdio JSON-RPC
- **S.11**:撤 `chariot/server/`(所有 surface 切完后才动)
- **S.12**:docs / README / CLAUDE.md 收尾 + 版本号 bump

---

## 验收标准统一格式

每步用以下 5 个维度判收(下文每步「验收」段照此填具体内容):

- **单测**:本步新增 / 改写的测试,关键断言要点(不是只列 `pytest` 命令)
- **静态**:`uv run ruff check .` + `uv run ruff format --check .` +
  `uv run pyright chariot/` 三件套全绿,**0 警告 0 错误**为基线
- **手测**:具体命令 + **预期看到的输出形态**(不是只写"跑跑看")
- **回归**:旧行为不变(过渡期共存的步骤特别重要),通过 `grep` 守不应残留的引用
- **不通过特征**:出什么形态就算挂了,作为 review 时的红旗清单

---

## 0.6.0 patch 列表

### S.1 ⏳ 核心类型铺底:`ChatRequest` / `ChatEvent` / 异常

**目标**:落地 Claude 形态的协议无关 IO 类型(跟 Claude Messages API 1:1)。
纯新增,不动现有代码。

- 新建 `chariot/agent/__init__.py`(空)
- 新建 `chariot/agent/chat_request.py`:
  - `Message`(`@dataclass(frozen=True)`,role / content;Claude 形态 content
    blocks list)
  - `SystemBlock`(type / text / cache_control;跟 Claude 一致)
  - `ToolSchema`(name / description / input_schema;跟 Claude 一致)
  - `ChatRequest`(`@dataclass(frozen=True)`)—— **跟 Claude API request body
    1:1 平铺 14 字段**(详见 DESIGN §3.1):
    - Claude 字段:`model` / `messages` / `max_tokens` / `system` / `tools` /
      `tool_choice` / `temperature` / `top_p` / `top_k` / `stop_sequences` /
      `metadata` / `thinking`
    - chariot 扩展:`convo_id` / `agent_id`
    - **不引入** `stream` / `service_tier` / `anthropic_version`(理由见 DESIGN)
- 新建 `chariot/agent/chat_event.py`:
  - `ChatEvent`(`@dataclass(frozen=True)`,`kind: Literal[...]` 10 种 +
    各 kind 专属字段)
  - **kind 直接采用 Claude SSE event 命名**:
    - 8 种 Claude 原生:`message_start` / `content_block_start` /
      `content_block_delta` / `content_block_stop` / `message_delta` /
      `message_stop` / `ping` / `error`
    - 2 种 chariot 自注:`tool_result` / `stream_done`
  - 字段:`message` / `index` / `content_block` / `delta` / `usage` /
    `error_type` / `error_message` / `tool_use_id` / `content` / `is_error`
    (全 optional,按 kind 取用)
  - 工厂方法可选(便于构造):`ChatEvent.text_delta(...)` /
    `ChatEvent.tool_result(...)` 等
- 新建 `chariot/agent/exceptions.py`:
  - `ProviderError(code: str, message: str, status: int = 502)` —— 替代旧
    `ServiceError` 的 Provider 范畴(去掉 fastapi 依赖)
  - `ConfigError`(沿用 0.5.0 语义,从 `server/config.py` 抽出)
  - `ToolExecutionError`、`ConvoLockTimeout`

**验收**:

- **单测**(`tests/agent/test_chat_event.py` / `test_chat_request.py` /
  `test_exceptions.py`):
  - `ChatEvent.text_delta("hi").kind == "content_block_delta"` + frozen
    (`event.kind = "x"` 抛 `FrozenInstanceError`)
  - `match event.kind:` dispatch 覆盖**全部 10 种 kind**:`message_start` /
    `content_block_start` / `content_block_delta` / `content_block_stop` /
    `message_delta` / `message_stop` / `ping` / `error` / `tool_result` /
    `stream_done` —— 漏一个静态分析能查到
  - `ChatRequest(model="claude", messages=[...])` 14 字段默认值符合 DESIGN §3.1;
    `ChatRequest` frozen
  - **跟 Claude API 互操作**:`ChatRequest(**claude_body_dict)` 能构造成功;
    `dataclasses.asdict(req)` 回来的 dict 字段名集合 ⊇ Claude API 字段集合
  - `ProviderError("upstream_auth_failed", "x")` `code` / `message` /
    `status=502` 字段;`exceptions.py` 里**无 fastapi import**
- **静态**:`uv run ruff check chariot/agent/ tests/agent/` +
  `uv run ruff format --check chariot/agent/ tests/agent/` +
  `uv run pyright chariot/agent/`,基线 0 警告 0 错误
- **手测**:
  - `uv run python -c "from chariot.agent.chat_event import ChatEvent; print(ChatEvent.text_delta('hi'))"`
    → 打印 `ChatEvent(kind='content_block_delta', delta={'type': 'text_delta', 'text': 'hi'}, ...)`
- **回归**:`uv run pytest -q` 全套绿,case 数 = 旧基线 + 新增(纯增量)
- **不通过特征**:
  - `grep -rn "from fastapi\|import fastapi" chariot/agent/` 非空
  - ChatEvent 子类化(应该是单一 dataclass + Literal kind,不是 ABC)
  - ChatRequest 字段名跟 Claude API 不一致(`model_name` 残留 / `sampling`
    嵌套残留 / 缺 `tool_choice`/`metadata`/`thinking`)
  - kind literal 名跟 Claude SSE 不一致(`turn_start` / `text` / `tool_use_*`
    等旧名残留)

### S.2 ⏳ Provider 抽象 + MockProvider

**目标**:`BaseProvider` ABC 落地 + 第一个实现(协议无关 mock)。

- 新建 `chariot/providers/__init__.py`
- 新建 `chariot/providers/base.py`:
  - `BaseProviderConfig`(`@dataclass(frozen=True)`,通用字段:name / model;
    `Base` 前缀表"子类可继承扩展")
  - `BaseProvider`(ABC):`from_options(options)` classmethod +
    `generate(req: ChatRequest) -> AsyncIterator[ChatEvent]` async 抽象方法
- 新建 `chariot/providers/registry.py`:`ProviderRegistry`(类比旧
  `ModelRegistry`,接 `BaseProvider` 子类;`register(type_name, cls)` /
  `build(entry) -> BaseProvider` / `known_types()`)
- 新建 `chariot/providers/builtin/__init__.py`
- 新建 `chariot/providers/builtin/mock.py`:`MockProvider`
  - 不发任何 HTTP / 不拼 SSE,直接 yield Claude 形态的 ChatEvent 序列:
    `message_start` → `content_block_start(text)` →
    `content_block_delta(text_delta)+` → `content_block_stop` →
    `message_delta(stop_reason=end_turn)` → `message_stop`
  - **不产 `stream_done`**(Provider 不管跨轮收敛,这是 AgentLoop 职责)
  - `from_options` 不消费任何字段(沿用旧 MockModel 行为)

**验收**:

- **单测**(`tests/providers/builtin/test_mock.py` /
  `tests/providers/test_registry.py`):
  - `[ev.kind async for ev in provider.generate(req)]` 至少含
    `["message_start", "content_block_start", "content_block_delta",
    "content_block_stop", "message_delta", "message_stop"]` 顺序;
    text_delta 内容含 req 末轮 user content 的 echo
  - `register("mock", MockProvider)` 后 `build(entry)` 返实例;二次注册
    同 type_name 抛 `ValueError("duplicate provider type: mock")`
  - `known_types()` 返集合含已注册 type_name
  - `BaseProvider` 不能直接实例化(`pytest.raises(TypeError)`),`generate`
    是抽象方法
- **静态**:三件套全绿
- **手测**:
  - `uv run python -c "import asyncio; from chariot.providers.builtin.mock import MockProvider; from chariot.agent.chat_request import ChatRequest, Message; p = MockProvider.from_options({}); req = ChatRequest(model='mock', messages=[Message(role='user', content='hi')]); asyncio.run((lambda: [print(e.kind) async for e in p.generate(req)])())"`
    → 依次打印 `message_start` / `content_block_start` / `content_block_delta`
    × N / `content_block_stop` / `message_delta` / `message_stop`
- **回归**:`uv run pytest -q` 全套绿;旧 `tests/server/test_mock_model.py`
  仍绿(MockProvider 是新增,不动旧 MockModel)
- **不通过特征**:
  - `BaseProvider.generate` 返同步迭代器(应 `AsyncIterator[ChatEvent]`)
  - MockProvider 产了 `turn_start` / `text` 等 chariot 内部命名(应该用
    Claude SSE 命名)
  - MockProvider 产了 `stream_done`(那是 AgentLoop 职责)

### S.3 ⏳ AnthropicProvider 重写 + SSE 共享

**目标**:从 `server/model/anthropic.py`(透传字节)重构成
`providers/builtin/anthropic.py`(SSE 解析后 yield Claude 形态 ChatEvent),
并把 `shared/sse.py` 迁到 `providers/_sse.py`。

- 新建 `chariot/providers/_sse.py`:
  - 内容沿用 `chariot/shared/sse.py`(SseParser / iter_frames),不动逻辑
  - 下划线前缀表示包内私用(给 builtin/ 各 Provider 实现用)
- 新建 `chariot/providers/builtin/anthropic.py`:`AnthropicProvider`
  - httpx client / timeout / api_key 解析逻辑沿用旧 `AnthropicModel`
  - `generate(req)`:body 从 `ChatRequest` 用 `dataclasses.asdict()` 拼装,
    去掉 chariot 扩展字段(`convo_id` / `agent_id`),其余 1:1 透传
  - SSE 解析复用 `chariot/providers/_sse.py` 的 `SseParser.iter_frames`
  - **SSE 帧 → ChatEvent 几乎 1:1**:Anthropic event type 直接当 ChatEvent kind,
    payload 字段(`message` / `index` / `content_block` / `delta` / `usage`)
    直接搬
  - 错误码映射:200 前的 401/403 → 抛 `ProviderError("upstream_auth_failed")`,
    5xx → `ProviderError("upstream_server_error")`,httpx exceptions →
    `upstream_unreachable`
  - **流式 200 已发后异常** → yield
    `ChatEvent(kind="error", error_type="upstream_stream_error")` + return,
    **不抛**(对标 0.1.0"200 已发后断 TCP"契约)
- `providers/__init__.py` 加
  `ProviderRegistry.register("anthropic", AnthropicProvider)` +
  `register("mock", MockProvider)`(收一处)

**验收**:

- **单测**(`tests/providers/builtin/test_anthropic.py`,httpx `MockTransport`
  喂 fixture SSE bytes):
  - **case A 纯 text**:断 `[ev.kind for ev in evs]` 形如
    `["message_start", "content_block_start", "content_block_delta",
    "content_block_delta", ..., "content_block_stop", "message_delta",
    "message_stop"]`;`content_block_start` 的 `content_block["type"]=="text"`;
    `content_block_delta` 的 `delta["type"]=="text_delta"`
  - **case B tool_use 流**:含 `content_block_start(content_block.type="tool_use",
    name="read_file")` + `content_block_delta(delta.type="input_json_delta",
    partial_json=...)` × N + `content_block_stop` +
    `message_delta(delta.stop_reason="tool_use")` + `message_stop`
  - **case C 401**:`with pytest.raises(ProviderError) as exc; assert
    exc.value.code == "upstream_auth_failed"`(200 前抛)
  - **case D 500**:`code == "upstream_server_error"`(200 前抛)
  - **case E `httpx.ConnectError`**:`code == "upstream_unreachable"`(200 前抛)
  - **case F 200 已发后中途 IO 错**:events 末尾出现
    `ChatEvent(kind="error", error_type="upstream_stream_error")`,流终止
    **不抛异常**
  - 删除 `tests/server/test_anthropic_model.py`(旧 case 已被覆盖)
- **静态**:三件套全绿
- **手测**(可选,留 reference,不阻断):
  - 有真 ANTHROPIC_API_KEY:
    `uv run python -c "import asyncio, os; from chariot.providers.builtin.anthropic import AnthropicProvider; ..."`
    → 收到真实 ChatEvent
- **回归**:`pytest -q` 全套绿;`grep -rn "AnthropicModel" chariot/server/`
  仅在 `chariot/server/model/anthropic.py` 自身(过渡期 server/agent.py 暂未切;
  S.6 才动)
- **不通过特征**:
  - SSE event 名硬编码字符串散在 `generate` 各处(应统一常量或 dispatch 表)
  - `httpx.HTTPError` / `ConnectError` / `ReadError` 任一漏抓
  - `kind="error"` event 后还继续 yield 别的 event
  - `chariot/shared/sse.py` 仍存在(应已迁,grep `from chariot.shared.sse`
    指向新位置)
  - error event 用了 `code=` / `message=` 字段(应该是 `error_type=` /
    `error_message=`)

### S.4 ⏳ Tool / Repo / Database 迁到新结构

**目标**:把跟 fastapi 无关的核心层从 `chariot/server/` 物理搬到顶层平铺
位置。搬完后**现有 server 仍能跑**(过渡共存),为后面撤 server 铺路。

- `git mv chariot/server/database/ chariot/database/`
- `git mv chariot/server/repository/ chariot/repos/`
  - 内部:`git mv chariot/repos/log.py chariot/repos/log_repo.py`
- `git mv chariot/server/service/log_writer.py chariot/repos/log_writer.py`
  (跟 LogRepo 同居,职责呼应)
- `git mv chariot/server/convo_lock.py chariot/agent/convo_lock.py`
- `git mv chariot/server/config.py chariot/agent/config.py`
  - 删 server-specific 字段(host / port / 等);config 现在只描述 ChariotConfig
    (`models` entries / `tools` entries)
- `git mv chariot/server/tool/ chariot/tools/`
  - 内部:把 `httpget.py` / `listdir.py` / `readfile.py` / `shellexec.py`
    全部 `git mv` 到 `chariot/tools/builtin/` 下,且改下划线命名:
    `http_get.py` / `list_dir.py` / `read_file.py` / `shell_exec.py`
  - `tools/base.py` 留顶层,**类名改 `Tool` → `BaseTool`**(明确抽象基类)
  - `tools/registry.py` 留顶层
- 改 import 路径:全仓 `from chariot.server.tool` → `from chariot.tools`,
  `from chariot.server.repository` → `from chariot.repos`,
  `from chariot.server.database` → `from chariot.database` 等。`chariot/server/agent.py`
  等仍引用,但路径都指新位置。`Tool` 类的 import 改成 `BaseTool`
- 测试目录镜像迁移:`tests/server/{tool,repository,database}/` →
  `tests/{tools,repos,database}/`(整目录 git mv;import 路径同步改;类名
  `Tool` → `BaseTool` 同步)

**验收**:

- **单测**:**测试 case 数前后完全相等**(只移位置不增减),全部仍绿
- **静态**:`uv run ruff check . && uv run ruff format --check . &&
  uv run pyright chariot/`,0 警告 0 错误
- **手测**:
  - `uv run python -m chariot.server` 仍能起,`/v1/messages` 仍工作(纯路径搬,
    行为不变)—— 用 0.5.0 既有的 mock 调用脚本(或 `curl localhost:<port>/v1/messages`)
    跑通一次 stateless chat
  - `uv run chariot tool list` / `model list` / `conversation list` 都仍好使
- **回归**(grep 守门):
  - `grep -rn "from chariot.server.tool\|from chariot.server.repository\|from chariot.server.database\|from chariot.server.conversation_lock" chariot/ tests/` = 0
  - `grep -rn "class Tool(" chariot/ tests/` = 0(应改成 `class BaseTool(`)
  - `grep -rn "from chariot\." chariot/server/` 应只引用顶层模块(server 反向依赖
    新结构,符合过渡期预期)
- **不通过特征**:
  - pyright 报 import 路径错(漏改)
  - 任一新位置文件仍 import `chariot.server.xxx`(只迁了一半)
  - server 启动时报 `ModuleNotFoundError`
  - `Tool` 类名残留(应该全部改成 `BaseTool`)

### S.5 ⏳ 双层锁(进程内 + 跨进程 SQLite advisory)

**目标**:为库化模式下"多进程并发同 convo_id"加 SQLite 层保护。

- `chariot/agent/convo_lock.py`:
  - 进程内 `ConvoLockManager`(asyncio.Lock 字典)沿用,不变
  - 加 `acquire(convo_id, *, db_session)` —— 同时拿进程内锁 + DB 锁
- `chariot/repos/convo_repo.py`:加方法
  `with_advisory_lock(convo_id, timeout_s)` —— async context manager,内部用
  SQLite `BEGIN IMMEDIATE`(短事务)包住"load history + append message"原子段;
  超时(默认 5s)自动重试 3 次;再失败抛 `ConvoLockTimeout`
- `chariot/server/agent.py`(过渡期仍存):`_stream_tool_loop` 把
  `ConvoLockManager.acquire` 替换成新的
  `lock_manager.acquire(convo_id, db_session=...)`

**验收**:

- **单测**(`tests/agent/test_convo_lock.py`):
  - **进程内并发**:`asyncio.gather` 起 10 个并发 `acquire(convo_id)`,critical
    section 内 list.append 后 `await asyncio.sleep(0.05)`,断结果列表是单调
    递增的 entry 顺序(实际串行,不交叉)
  - **跨进程并发**:`multiprocessing.Process` 起 2 个 worker,各自打开 SQLite
    session 同 convo_id,worker_2 SELECT 拿到 worker_1 的 INSERT 数据
  - **DB 锁超时**:monkeypatch `BEGIN IMMEDIATE` `busy_timeout` 为 0.1s,
    worker_a 持锁 1s,worker_b `acquire` → 重试 3 次后抛
    `ConvoLockTimeout`
  - **错误事件**:进程内锁超时 / DB 锁超时分别产
    `ChatEvent(kind="error", error_type="convo_busy_local")` /
    `error_type="convo_busy_db"`(对接 §7.3)
- **静态**:三件套全绿
- **手测**(可选):起两个 `python -m chariot.server` 实例同 DB,并发打同 convo_id
  10 次,看 logs 表无脏数据(全部 ULID 单调 + content 正确)
- **回归**:0.5.0 既有 `ConvoLockManager` 单测条数不变,全绿
- **不通过特征**:
  - 测试卡死(死锁)→ pytest hang
  - 子进程没正确 cleanup(pytest 退出码非 0,但测试本身报 pass)
  - `with_advisory_lock` 内吞了 `OperationalError` 没区分 timeout vs 别的 DB 错
  - error event 用了 `code=` 字段(应该 `error_type=`)

### S.6 ⏳ AIAgent + AgentLoop(协议无关重写)

**目标**:`chariot/agent/run.py` + `chariot/agent/loop.py` 落地。
**先共存,不撤旧 `server/agent.py`** —— 让 server 仍能 import 旧 Agent 跑(过渡)。

- 新建 `chariot/agent/loop.py`:`AgentLoop` 类(详见 DESIGN §6.2 / §6.4)
  - `__init__(provider, tools, repo, convo_id, max_iter)`
  - `async def run(req: ChatRequest) -> AsyncIterator[ChatEvent]`:
    - 每轮 `provider.generate(req)` → 实时透传 + 内部累积 assistant content blocks
    - 收 `message_delta` 里的 `stop_reason`
    - `message_stop` 后:持久化 → 判 stop_reason
      - `tool_use` → 跑工具 → yield `tool_result` events(chariot 注入)→
        拼下轮 req → 续
      - 其它(`end_turn` / `max_tokens` / `stop_sequence`)→ yield
        `stream_done` → break
    - max_iter 超 → yield
      `ChatEvent(kind="error", error_type="agent_iter_exceeded")`
- 新建 `chariot/agent/run.py`:`AIAgent` 类
  - `__init__(*, providers: dict[str, BaseProvider], tools: dict[str, BaseTool],
    repo, lock_manager)`
  - `from_db(db_path) -> Self` classmethod —— 装载所有 ModelEntry 构造 Provider /
    所有 ToolEntry 构造 Tool
  - `async def run(req: ChatRequest) -> AsyncIterator[ChatEvent]`:
    - 路由:按 `req.model` 找 provider(缺失 → yield
      `error(error_type="unknown_model")`)
    - server-side default tools 注入(`req.tools is None` → 挂所有 enabled
      tools 的 schema)
    - stateful(`req.convo_id`)→ `lock_manager.acquire(...)` 包住 AgentLoop
    - stateless → 直接跑 AgentLoop
- 新增 `chariot/providers/prober.py`:`ProviderProber`(原 `service/model_prober.py`
  迁 + 改名,适配新 `BaseProvider` 接口)

**验收**:

- **单测**(`tests/agent/test_loop.py` / `test_run.py` /
  `tests/providers/test_prober.py`):
  - **AgentLoop 多轮**:mock provider 给 3 轮序列(轮1: text + tool_use,
    轮2: text + tool_use,轮3: text-only 收尾),断 `[ev.kind for ev in events]`
    含 3 个 `message_start` + 2 个 `tool_result` + 1 个 `stream_done`
  - **AgentLoop 工具异常**:tool 抛 `ValueError("x")` → events 含
    `tool_result(is_error=True)` 且 content 包 "x"
  - **AgentLoop max_iter 超**:`max_iter=2` + provider 永远给 stop_reason=tool_use
    → events 末尾 `error(error_type="agent_iter_exceeded")`
  - **AgentLoop input_json 拼装**:provider 流 `input_json_delta` 三片
    `'{"path":'` `'"/tmp"'` `'}'` → AgentLoop 在 `content_block_stop` 拿到
    `{"path": "/tmp"}` 完整 dict;若拼装失败 → `tool_result(is_error=True,
    content="invalid_json: ...")`
  - **AIAgent stateless**:无 convo_id → mock lock_manager.acquire 调用次数 = 0
  - **AIAgent stateful**:有 convo_id → mock lock_manager.acquire 被调一次
  - **AIAgent 路由**:`req.model="not_exists"` → 单 event
    `error(error_type="unknown_model")`
  - **AIAgent default tools 注入**:`req.tools is None` → AgentLoop 拿到 tools
    列表 = enabled tool 全集
  - **ProviderProber**:对 mock provider 执行 probe → 返成功;对故意抛错的
    provider → 返 ProviderError 包装
- **静态**:三件套全绿
- **手测**:
  - `uv run python -c "import asyncio; from chariot.agent.run import AIAgent; agent = AIAgent.from_db('~/.chariot/chariot.db'); ..."`
    → 跑出非空 ChatEvent 序列
- **回归**:旧 `tests/server/test_agent.py`(0.5.0)整体仍绿(过渡期 server/agent.py
  不动)
- **不通过特征**:
  - AgentLoop 内调 `httpx` / `fastapi` / SSE 字节(应纯 ChatEvent in/out)
  - AIAgent.run 没把 conv 锁裹住 AgentLoop(stateful 路径里 `lock_manager.acquire`
    应包整个 AgentLoop.run)
  - AgentLoop / AIAgent 间出现循环 import
  - error event 用了 `code=` / 旧 error_type 字符串(`tool_iter_exceeded` 等
    旧名)

### S.7 ⏳ CLI 切到直接 import core(撤 sdk/ + cli/core/ + daemon 命令)

**目标**:CLI 不再走 HTTP `/v1/messages`,直接进程内构造 AIAgent;撤 SDK 整目录;
撤 `cli/core/` 子目录(平铺到 `cli/`);撤 daemon 时代命令。

- `chariot/cli/core/` 整目录扁平化:
  - `git mv chariot/cli/core/context.py chariot/cli/context.py`
  - `git mv chariot/cli/core/render.py chariot/cli/render.py`
  - `git mv chariot/cli/core/repl.py chariot/cli/repl.py`
  - `git mv chariot/cli/core/batch.py chariot/cli/batch.py`
  - `git mv chariot/cli/core/once.py chariot/cli/once.py`
  - `chariot/cli/context.py`:撤 `ProxyClient` / `ChatStream`,持有 `AIAgent`
    实例;`run_turn` / `events` 调 `agent.run(req)`
  - `chariot/cli/repl.py` / `render.py`:消费 Claude 形态 ChatEvent
    (`content_block_delta(text_delta)` → stream_token,`content_block_stop`
    对应 tool_use → tool_use_line,`tool_result` → tool_result_line,
    `message_stop` → newline)
- `chariot/cli/commands/`:
  - `chat.py`:delegate 到 `cli/repl.py` / `batch.py` / `once.py`(根据 flag)
  - `model.py`:`add` / `edit` / `rm` / `list` / `probe` 改直接调
    `chariot/repos/model_repo.py` + `chariot/providers/prober.py`(原
    server/controller/models.py 业务迁到 repo)
  - `tool.py` / `conversation.py` / `logs.py`:同上,调对应 repo
  - **撤** `start.py` / `stop.py`(库化无 daemon)
  - **改** `status.py`:从"问 daemon 状态"→"显示 DB 路径 / provider 数 /
    tool 数 / 版本"
  - `stats.py`:沿用(logs 表统计)
- 撤 `chariot/sdk/` 整目录(`git rm -r chariot/sdk/`)
- 撤 `tests/sdk/` 整目录

**验收**:

- **单测**(`tests/cli/test_repl.py` 等重写):
  - `chariot chat "hi"` 用 `MockProvider` + 内存 SQLite 跑,stdout 含
    mock echo + 退出码 0
  - `chariot model add foo --type=mock` → repo 多一行;`chariot model rm foo`
    → 减一行
  - `chariot tool list` / `tool config xxx` / `conversation list / rename / delete`
    → 各自调 repo,stdout 形态符合 0.5.0 基线
  - `chariot status` → 输出含 "DB:" / "providers:" / "tools:" / "version:" 关键字
- **静态**:三件套全绿
- **手测**:
  - `uv run chariot chat "hello"` → 看到 mock provider echo;**无 connection
    refused**(已不连 server)
  - `uv run chariot model list` 看到 entries
  - `uv run chariot conversation list` / `tool list` / `status` / `stats` 都好使
  - `uv run chariot start` → 应报 `Error: No such command 'start'`
  - `uv run python -m chariot.server` **仍能起**(过渡期没撤 server),`/v1/messages`
    仍工作
- **回归**(grep 守门):
  - `grep -rn "from chariot.sdk" chariot/ tests/` = 0
  - `tests/sdk/` 整目录已删
  - `chariot/cli/core/` 目录已撤(应不存在)
  - `grep -rn "ProxyClient\|ChatStream" chariot/cli/` = 0
- **不通过特征**:
  - CLI 启动 import `chariot.agent` 慢(>500ms)
  - `chariot chat` 卡住 / 退出码非 0
  - 残留 SDK import 导致 pyright 报错
  - `chariot status` 仍显示 "daemon pid" / "endpoint" 等老字段
  - Renderer 用旧 ChatEvent 命名(`text` / `tool_use_done` 等)

### S.7.1 ⏳ 默认 provider 机制 + AnthropicProvider body.model 改写

**目标**:让 `chariot chat` 不带 flag 也能跑(走 DB 默认 provider);顺手修
`AnthropicProvider` 漏改 `body.model` 的 bug(0.6.0 重写时丢的 0.5.0 行为)。

S.7 砍了 `--model "claude-haiku-4-5"` hardcoded 默认,但没提供替代,导致
`chariot chat "hi"` 直接 die。S.7.1 把 S.7 留下的"运行链断点"补全。

- **schema v7**(`chariot/database/migrations/007_provider_default.sql`):
  `providers` 加 `is_default INTEGER NOT NULL DEFAULT 0`;约束:同时至多一行 = 1
  (由 `ProviderRepo.set_default` 原子保证)
- **`ProviderRepo` 加方法**:
  - `get_default() -> ProviderEntry | None`
  - `set_default(name)`(原子清所有 + 置选中;不存在 → `ProviderNotFound`)
  - `unset_default()`(no-op 友好)
- **CLI 加命令**(`chariot/cli/commands/provider.py`):
  - `chariot provider use <name>`:设默认
  - `chariot provider show [<name>]`:不带参数 = 当前默认;带参数 = 指定 entry
    详情(`api_key` 打码 `(set, len=N)`,`api_key_env` 原值)
  - `chariot provider list` 加 `default` 列(`*` 标记)
- **CLI chat 改造**(`chariot/cli/commands/chat.py`):
  - 砍 `--model`,改 `--provider <name>`
  - 不传 `--provider` → 查 DB 默认 → 没默认 → die 提示
  - 砍 `cli/context.py` 里 `DEFAULT_MODEL = "claude-haiku-4-5"` 常量
- **`ChatContext.model` → `provider_name`** rename(对齐 v6 的"model → provider"
  系列;`ChatRequest.model` 字段名沿用 Claude API 习惯不动,只是内容是
  chariot 路由 key)
- **REPL slash 加 `/provider use <name>`**:持久化到 DB;`/providers` 输出加
  `default` 列
- **status 改造**:从 "DEFAULT_MODEL 常量是不是 registered" 改为 "查 DB 默认行 +
  是否在已加载 providers 字典里"
- **AnthropicProvider bug 修复**:`_build_body` 从 `@classmethod` 改实例方法,
  加 `body["model"] = self.config.model`(0.5.0 旧 `chariot/server/model/anthropic.py`
  里有这行,0.6.0 重写丢了)。漏掉这步会让 `body.model` 透传 chariot 的 entry
  name 给上游,Anthropic 报 `not_found_error: model 'xxx' not found`

**验收**:

- **单测**(`tests/server/test_provider_repo.py`,8 个新 case):
  - 空表 → `get_default()` 返 None
  - 表非空但无行标 1 → 返 None
  - `set_default(b)` → `get_default()` 返 b
  - 连续 `set_default(a)` → `set_default(b)` → 只有 b 是 default(原子切换)
  - `set_default(a)` 重复调幂等(只一行 = 1)
  - `set_default("ghost")` → `ProviderNotFound`,失败不误改其它行
  - `unset_default` → `get_default()` 返 None
  - `unset_default` 无默认时 no-op 不抛
- **单测**(`tests/cli/test_repl_slash.py`,3 个新 case):
  - `/provider <name>` 只改本地 ctx,DB 默认未受影响
  - `/provider use <name>` 设 DB 默认 + 同步本地 ctx
  - `/provider use <unknown>` 报 error,ctx 不变
  - `/providers` 输出含 `*` default 标记 + ← current 标记(各只一次)
- **单测**(`tests/cli/test_commands.py`):
  - `chariot provider --help` 列出 list/show/use/probe/add/edit/rm/copy
  - `chariot provider use`(不带参数)→ exit_code != 0
  - `chariot chat --help` 含 `--provider`
  - `chariot chat --model anything hi` → exit_code != 0(`--model` 已下线)
- **单测**(`tests/providers/builtin/test_anthropic.py` 新 case):
  - `_build_body` 把 `body["model"]` 改写成 `self.config.model`(回归)
- **静态**:三件套全绿
- **手测**:
  - `chariot provider use <name>` → DB 反映;`status` 显示该 default
  - `chariot chat "hi"`(已设默认)→ 跑通,body.model = entry.options.model
  - `chariot chat "hi"`(未设默认 + 不传 flag)→ stderr 含提示
  - `chariot provider show` → 显示当前默认;`provider show <name>` → 显示指定;
    api_key 打码 `(set, len=N)`,不泄露内容
- **不通过特征**:
  - `_build_body` 没改 body.model → 上游 404 not_found_error
  - `set_default` 不是原子(多行 = 1)
  - `--model` flag 还在
  - REPL `/provider <name>` 误改 DB(应只改本地)
  - status 还引用 `DEFAULT_MODEL` 常量

### S.7.2 ⏳ ChatRequest.model → provider_name(IR 字段命名跟 wire 解耦)

**目标**:把 `ChatRequest.model` 重命名为 `provider_name`,跟 v6 rename 系列
(model→provider 在 DB / Repo / CLI 层)在 IR 层收尾。同时把 wire 字段名 `model`
彻底归到 Provider 内部职责(`_build_body` 时从 `self.config.model` 显式写)。

**为什么不就叫 `provider`**:CLI flag 用短名 `--provider`(贴近用户),IR /
内部参数传递用 `provider_name`(避免跟 wire `body.model`、`BaseProvider` 实例
对象同名歧义);ChatContext / ChatRequest 等内部数据结构都用长名,看到字段名
就知道"这是 provider 的 *名字*,不是 provider 实例本身,也不是 wire model id"。

**原 0.6.0 设计**:`ChatRequest.model` 字段名沿用 Claude API,装的是 chariot
路由用的 entry name。这个错位带来三个后果:

1. AnthropicProvider 必须在 `_build_body` 里把 `body["model"] = self.config.model`
   改写一次(0.6.0 重写时漏过,S.7.1 修复)
2. 未来 `OpenAIProvider` 接入时这个字段名更尴尬 —— `ChatRequest` 是 chariot 自己
   的 IR,不应跟某一家 API 字段名绑死
3. 跟 v6 的 model→provider rename 不齐:DB 表 / `ProviderRepo` / `ProviderEntry` /
   CLI `--provider` / `ChatContext.provider_name` 全部已经 rename,只剩 IR 这层
   还叫 `model`

**改动**:

- `chariot/agent/chat_request.py`:`model: str` → `provider_name: str`(必填字段
  排最前);docstring 说明命名约定 + wire `model` 由 Provider 内部写
- `chariot/agent/run.py`:`req.model` → `req.provider_name`(路由查询);
  `error_type="unknown_model"` → `"unknown_provider"`,文案 `unknown model entry`
  → `unknown provider entry`
- `chariot/agent/chat_event.py`:error_type 文档列表更新
- `chariot/providers/builtin/anthropic.py`:
  - `_CHARIOT_EXTENSION_FIELDS` 加 `"provider_name"`(从 dict 剔除,不进 wire body)
  - `_build_body` 显式 `body["model"] = self.config.model`(沿用 S.7.1,docstring
    更新)
- `chariot/providers/prober.py`:`ChatRequest(model=entry.name, ...)` →
  `ChatRequest(provider_name=entry.name, ...)`
- `chariot/cli/context.py`:`_build_request` 用 `provider_name=self.provider_name`
- `chariot/cli/commands/chat.py`:docstring `unknown_model` → `unknown_provider`

**验收**:

- **单测**(`tests/agent/test_chat_request.py`,新加 case):
  - `ChatRequest` dataclass 字段集合不含 `model`,含 `provider_name`
  - `dataclasses.asdict(req)` 字段集合 ⊇ Claude 结构字段,但**不**含 wire `model`
- **回归**(`tests/providers/builtin/test_anthropic.py` 已有 case 改名):
  - `_build_body` 把 `body["model"]` 从 `self.config.model` 写入(沿用 S.7.1)
  - body 不含 `provider_name` / chariot entry name 字符串
- **路由测试**(`tests/agent/test_run.py`):
  - `req.provider_name="not_exists"` → `error_type="unknown_provider"`
- **bulk rename** 影响所有 `tests/agent/` / `tests/providers/` / `tests/cli/`
  里 `ChatRequest(model=...)` 写法
- **静态 + 全套 pytest 全绿**
- **手测**:`chariot chat --provider mock "hi"` 跑通;`--provider not_exists`
  → stderr 含 `unknown_provider`
- **不通过特征**:
  - `chariot/` 下 `grep "req\.model\b"` 还有结果(必须全部改 `req.provider_name`)
  - `ChatRequest(model=` 还在(应该全部 `ChatRequest(provider_name=`)
  - error_type 仍叫 `unknown_model`

### S.7.3 ⏳ CLI per-call override:`--model` / `--base-url` / `--api-key`

**目标**:`chariot chat` 和 `chariot provider probe` 加三个 per-call CLI flag,
让用户不动 DB entry 就能临时换 LLM model id / 上游 URL / API key。常见场景:
快速切换 Claude 变体(`--model claude-opus-4-5`)、调试代理(`--base-url ...`)、
临时 key(`--api-key $TEMP`)。

**优先级链**(每字段独立):

| 字段 | 优先级 |
|---|---|
| `--model` | CLI flag → `entry.options.model`(无 env 兜底,Anthropic 没标准 env 名) |
| `--base-url` | CLI flag → `entry.options.base_url`(inline) → `ANTHROPIC_BASE_URL` env → 默认 `https://api.anthropic.com` |
| `--api-key` | CLI flag → `entry.options.api_key`(inline) → `entry.options.api_key_env` 指向的 env(默认 `ANTHROPIC_API_KEY`) |

按 Anthropic SDK 主流约定(inline > env);CLI flag 通过"注入到 options.model /
base_url / api_key inline"实现最高优先级。

**改动**:

- `chariot/providers/builtin/anthropic.py`:
  - 加 `_BASE_URL_ENV = "ANTHROPIC_BASE_URL"` 常量(对齐 Anthropic Python SDK)
  - 抽 `_resolve_base_url(options)` static 方法,优先级 inline → env → 默认;
    空串 inline → ConfigError(同 `_resolve_api_key` 校验)
  - `from_options` 改用 `_resolve_base_url`(替代 `options.get("base_url",
    DEFAULT)`)
- `chariot/agent/run.py`:`AIAgent` 加 `patch_provider_options(name, *,
  options_overrides)` 方法 —— 浅 merge `entry.options` + 重建 Provider 实例,
  替换 `_providers[name]`。空 patch / 未知 name 静默 no-op;`from_options` 抛
  ConfigError 透传给 caller
- `chariot/cli/commands/chat.py`:加 `--model` / `--base-url` / `--api-key` 三个
  typer flag;`_run` 收集为 patch dict,resolve provider 后调
  `agent.patch_provider_options`。ConfigError → `Renderer.die`
- `chariot/cli/commands/provider.py`:`probe_cmd` 加同样三个 flag;
  `dataclasses.replace(entry, options={**entry.options, **patch})` 后跑探针
  (探针走临时 entry,不改 DB / 不改 agent._providers)

**验收**:

- **单测**(`tests/providers/builtin/test_anthropic.py`,4 个新 case):
  - `_resolve_base_url`:options 无 inline → 读 `ANTHROPIC_BASE_URL` env
  - inline > env(优先级)
  - inline + env 都无 → `https://api.anthropic.com` 默认
  - 空串 inline → ConfigError(校验文案含 `base_url`)
- **单测**(`tests/agent/test_run.py`,5 个新 case):
  - `patch_provider_options(name, options_overrides={})` no-op(同实例)
  - `patch_provider_options("ghost", ...)` 静默不抛(让 AIAgent.run 路由层报)
  - 注入 `model` → 重建后 `provider.config.model` 反映新值
  - 注入 `base_url` → 重建后 `provider._base_url` 反映新值
  - 非法 override(空串 base_url)→ `ConfigError` 透传
- **CLI 测试**(`tests/cli/test_commands.py`,2 个 help-flag case):
  - `chariot chat --help` 含 `--model` / `--base-url` / `--api-key`
  - `chariot provider probe --help` 含同样三个 flag
- **静态**:三件套全绿
- **手测**:
  - `chariot chat --model claude-opus-4-5 "hi"`(已设默认 provider)→ 跑通,
    上游收到 body.model = `claude-opus-4-5`
  - `chariot chat --provider claude --base-url https://proxy.test/ "hi"`
    → 走代理 URL
  - `ANTHROPIC_BASE_URL=https://env.test/ chariot chat "hi"`(entry 无 inline
    base_url)→ 走 env URL
  - `chariot provider probe claude --api-key $TEMP_KEY` → 用临时 key 探针,
    DB 不改
- **不通过特征**:
  - CLI flag 没注入(`agent.patch_provider_options` 没调)
  - `_resolve_base_url` 优先级反了(env > inline)
  - 空 patch dict 也重建 Provider(浪费 + 干扰单例 hashing)
  - probe 的 override 落到 DB(应只改临时 entry,不动 ProviderRepo)

---

## 0.6.5 patch 列表(2026-05 增量)

> **背景**:0.6.0 落地后审视长跑场景(sidecar / Gateway / 高并发)发现三处架构
> 盲区:① Provider 实例持 httpx client → per-call override 重建 client →
> 连接池频繁 churn;② AIAgent 单例 → 同进程不能服务多个 session;③ 0.6.0
> 把 `ChatRequest.model` 字段去掉转 `provider_name` 后,per-call 切 LLM id 也
> 必须走 Provider 重建路径,过度。
>
> **对照参考**:hermes-agent 的 stateless `ProviderTransport` + 进程级
> `_client_cache` + per-session `_agent_cache` + 正交 `CredentialPool`
> 模式。0.6.5 在 chariot 里做对应实现。
>
> **0.6.5 主题**:**框架修正,绝不留技术债**(用户原话)。Surface / 工具 / DB
> schema / IR(除 `model` 字段加回)全部不变,只改 Provider / Client / Agent
> 三层的生命周期边界。

### S.0 ✅ ClientCache + ClientSpec(进程级 httpx client 缓存)

- 新建 `chariot/providers/clients.py`:`ClientSpec`(frozen dataclass,hashable)
  + `ClientCache`(LRU 16,asyncio.Lock 并发安全)
- evict 不调 `client.aclose()`(避免中断 in-flight);进程退出靠 `aclose_all`
- 新增 `tests/providers/test_clients.py` 14 个 case:
  - `ClientSpec` frozen / hashable / 同字段相等
  - `ClientCache.get` 命中 / 未命中
  - LRU evict(超 16 弹最老,并发 evict 不出锁竞争)
  - 同 spec 并发 acquire → 不重复建
  - `aclose_all` 关全部 + 幂等

### S.1 ✅ BaseProvider 接口改造 + AnthropicProvider/MockProvider 重写

- `BaseProvider.__init__` 不再 `(*, config, api_key, base_url, client)`,
  改 `(*, config, options)` —— 所有实现自己从 options 解析 + 构造 ClientSpec
- 撤 0.6.0 短暂引入的 `BaseProvider.aclose()` ABC(Provider 不再持有可关资源)
- `AnthropicProvider`:
  - `_build_spec()` 从 `self._options` 算 ClientSpec(构造时一次)
  - `generate(req)` 内部 `client = await ClientCache.get(self._spec)`
  - `_build_body` 改 `body["model"] = req.model or self.config.model`
  - 加 `max_connections` / `max_keepalive` options(默认 20 / 10,Gateway 高并发可调)
- `MockProvider`:options 容忍任意字段(测试用)
- `tests/providers/builtin/test_anthropic.py`:`_make_provider` 改 monkeypatch
  `ClientCache.get` 注入 MockTransport 客户端;新增 per-call `req.model` 优先
  + 回退到 `config.model` 两个 case

### S.2 ✅ AIAgent 撤单例 + AgentRegistry per-session 缓存

- 撤 `AIAgent._current` ClassVar / `current()` / `uninstall()` /
  `patch_provider_options()`(S.7.3 临时方案)
- 新建 `chariot/agent/registry.py`:`AgentRegistry`(LRU 32,asyncio.Lock)
- `AIAgent.from_db(db_path, *, provider_overrides=None)` —— 装载时一次性把
  `overrides[name]` merge 进 `entry.options`,落 Provider.from_options
- `init_db` 改幂等(同 path 复用 engine);多 AIAgent 共享 engine
- `tests/agent/test_registry.py` 10 个 case;`tests/agent/test_run.py` 改写
  `TestPatchProviderOptions` → `TestFromDbProviderOverrides`

### S.3 ✅ CLI surface 适配(撤 patch_provider_options)

- `chariot/cli/_runtime.py`:`installed_runtime(provider_overrides=...)` ——
  → `AgentRegistry.acquire("process", db_path=..., provider_overrides=...)`;
  退出顺序 `AgentRegistry.aclose_all` → `ClientCache.aclose_all` → `dispose_db`
- `chariot/cli/commands/chat.py`:撤 `agent.patch_provider_options(...)` 调用,
  改预处理把 `--base-url` / `--api-key` 收成 `provider_overrides` dict(`--model`
  不在,见 S.4)
- 全套测试 506 → 506 不变(`AIAgent.uninstall()` 引用从 fixture 移除)

### S.4 ✅ ChatRequest.model 字段加回 + per-call wire 覆盖路径

- `ChatRequest` 加 `model: str | None = None`(0.7.0+ 路标的提前实现)
- `ChatContext` 加 `model_override: str | None = None`,_build_request 透传
  到 `ChatRequest.model`
- `AnthropicProvider._build_body`:`body["model"] = req.model or self.config.model`
- `chariot chat --model X`:不再走 `provider_overrides` 路径(改 ChatContext
  per-call),保留 `--base-url` / `--api-key` 走 provider_overrides
- 新增断言:`per_call_model_override_takes_precedence` /
  `per_call_model_none_falls_back_to_config`(`tests/providers/builtin/test_anthropic.py`)
- 全套测试 508 全绿(原 506 + 2 新 case)

### S.5 ⏳ 文档全套(DESIGN §5 重写 + 0.6.5 主题段 + 版本号 bump)

- `docs/DESIGN.md`:
  - 顶部 banner 加 0.6.5 主题段(增量 vs 0.6.0)
  - §3.1 ChatRequest 加 `model` 字段说明 + per-call 设计动机
  - §5 整章重写(四层生命周期 + ClientCache / ClientSpec / BaseProvider 新接口
    / 三种 override 路径 / AgentRegistry)
  - §6.1 AIAgent 类描述加并发约束 + from_db 新签名
- `docs/FEATURE.md`:本节(0.6.5 patch 列表 S.0~S.5)
- 版本号 bump:`pyproject.toml` / `chariot/__init__.py`(若有)
- README 不动(用户层无 visible 变化,只是底层架构)

**验收**:

- **单测**:全套 ≥ 508 绿(本步只动文档,不应改测试结果)
- **静态**:三件套全绿
- **手测**:
  - `chariot chat --model claude-haiku-4-5 "hi"` → 上游 body.model = haiku,**不**触发 Provider 重建
  - `chariot chat --base-url https://proxy.test/ "hi"` → Provider 重建,
    ClientCache 新 spec(若历史无相同 spec)
  - `chariot chat --model X --base-url Y "hi"` → 两条路径同时生效:Provider
    重建用 Y,wire body.model = X
- **回归**:0.6.0 验收命令仍通(`chariot status` / `provider list` / `chat` /
  REPL / `--convo`);DB schema / providers 表内容不变
- **不通过特征**:
  - DESIGN §5 还在讲 0.6.0 的"Provider 持 client"模型
  - FEATURE.md 没立 0.6.5 段
  - 版本号没 bump

### S.8 ⏳ rpc/jsonrpc.py + sidecar 新建(stdio JSON-RPC)

**目标**:JSON-RPC 框架放 `chariot/rpc/`(给后续 sidecar / acp / mcp 共享);
Tauri 用的 sidecar 进程落地。

- 新建 `chariot/rpc/__init__.py`
- 新建 `chariot/rpc/jsonrpc.py`:
  - `JsonRpcServer`:newline-delimited JSON 帧 reader / writer
  - 协议:request `{"id":..., "method":..., "params":...}` /
    response `{"id":..., "result":...}` / `{"id":..., "error":...}` /
    notify `{"method":..., "params":...}`(无 id,server 主推)
  - 错误码常量(-32700 ParseError / -32601 MethodNotFound 等)
  - dispatch 接口:`server.method("name")(handler)` 装饰器风格
- 新建 `chariot/sidecar/__init__.py`
- 新建 `chariot/sidecar/__main__.py`:entry,`asyncio.run(main())`,起 stdio
  JSON-RPC 主循环(import `chariot.rpc.jsonrpc.JsonRpcServer`)
- 新建 `chariot/sidecar/methods.py`:业务方法 dispatch
  - `chat(req: ChatRequest)` —— 启 `AIAgent.run(req)`,每个 ChatEvent 通过
    `notify("chat_event", dataclasses.asdict(event))` 推回;最后
    `return {stream_id, ended_at}`
  - `list_conversations()` / `get_conversation(id)` / `rename_conversation(id, title)`
    / `delete_conversation(id)`
  - `list_tools()` / `enable_tool(name)` / `disable_tool(name)` /
    `config_tool(name, options)`
  - `list_models()` / `add_model(...)` / `edit_model(...)` / `delete_model(...)` /
    `probe_model(name)`
  - `list_logs(filters)`

**验收**:

- **单测**(`tests/rpc/test_jsonrpc.py` / `tests/sidecar/test_methods.py` /
  `tests/sidecar/test_chat_method.py`):
  - 喂 `b'{"id":1,...}\n{"id":2,...}\n'` → 解出 2 帧
  - 半帧拼接:`b'{"id":1'` 后 `b':1}\n'` → 解出 1 完整帧
  - 错误帧 `b'not json\n'` → server 回 `{"id":null, "error":{"code":-32700, ...}}`
    (JSON-RPC 标准 ParseError)
  - 每个 method dispatch 一次,断 result schema(用 `isinstance` /
    `dataclasses.is_dataclass`)
  - `chat` method:mock AIAgent 给 N 个 ChatEvent(Claude 形态)→ reader 收到
    N 个 `notify("chat_event")` payload,断 payload 字段是 Claude 形态
    (`kind` 是 `message_start` / `content_block_*` 等)+ 1 个
    `response(id=1, result={stream_id, ended_at})`
- **静态**:三件套全绿
- **手测**:
  - `echo '{"id":1,"method":"list_conversations","params":{}}' | uv run python -m chariot.sidecar`
    → stdout 第一行 `{"id":1,"result":[...]}`(JSON 单行,以 `\n` 结尾)
  - `echo '{"id":1,"method":"chat","params":{"model":"mock","messages":[{"role":"user","content":"hi"}]}}' | uv run python -m chariot.sidecar`
    → stdout 多个 `{"method":"chat_event",...}` 行(payload kind 是 Claude 形态)
    + 末尾 `{"id":1,"result":{"stream_id":"...","ended_at":...}}`
- **回归**:server 仍能跑(过渡期);`pytest -q` 全套绿
- **不通过特征**:
  - stdin EOF 后 sidecar 不退出(应自然 graceful exit)
  - JSON-RPC 帧粘连 / 半帧丢失
  - chat method 漏推 ChatEvent / 推完不发 response
  - `rpc/jsonrpc.py` 引用了 `sidecar/` 任何东西(框架不应反向依赖业务层)
  - chat_event payload 用了 chariot 旧命名(`turn_start` / `text` 等),应是
    Claude 形态

### S.9 ⏳ Tauri Rust 切到 stdio JSON-RPC

**目标**:Tauri 后端进程从"启 server.exe + httpx 调 HTTP"改为"spawn
chariot-sidecar.exe + bidirectional stdio"。

- `packages/desktop/src-tauri/src/main.rs`(及关联模块):
  - 撤现有"sidecar 起 server + 读 endpoint.json"逻辑
  - 用 `tauri-plugin-shell` 的 `Command::sidecar(...).spawn()` 起
    `chariot-sidecar.exe`,拿 `(child, rx)`(stdout 流) + child stdin
  - 实现 `JsonRpcClient`:维护 pending requests map + writer task + reader task
    - reader:newline split → JSON parse → 若有 id → resolve pending;若无 id
      (notify)→ Tauri `app.emit("chat_event", payload)`
  - Tauri command `rpc(method: String, params: serde_json::Value) -> Result<Value>`
    —— 写 request 到 stdin,返 future 等 reader resolve
  - sidecar exit 监听:emit `event("sidecar_exited", reason)` 给前端,显示降级 UI
- `scripts/build.py`:`--target` 默认 / 唯一变成 `sidecar`,撤 `--target server`
  分支
- `chariot-server.spec` → `chariot-sidecar.spec`(PyInstaller spec 改 entry,
  module 改为 `chariot.sidecar`)

**验收**:

- **单测**:
  - Rust `cargo test`(若有现成 case),全绿
  - 新增 `JsonRpcClient` 的 unit:mock child stdout / stdin → 模拟一次 request /
    response 闭环 + 一次 notify 投递到 listener
- **静态**:`cargo clippy --all-targets -- -D warnings` 0 警告;Rust fmt
  `cargo fmt --check` 通过
- **手测**:
  - `uv run --group build python scripts/build.py --target sidecar --sync-sidecar`
    → 产出 `chariot-sidecar.exe` 在
    `packages/desktop/src-tauri/binaries/`
  - `bun run --filter=@chariot/desktop tauri dev` → Rust log 关键字依次出现:
    `spawned chariot-sidecar (pid=...)` /
    `rpc request method=list_conversations id=1` /
    `rpc response id=1 ok`
  - 关闭 app → log 含 `sidecar exited code=0`(自然退,非 kill)
- **回归**:`packages/app/` 此时还没切,UI 不工作正常(S.10 才切);只验 Rust
  层 + sidecar 能 RPC
- **不通过特征**:
  - sidecar 启动失败(stderr 报错) / pid 不复返
  - RPC pending 永远 unresolved(reader 没接对 id)
  - app 关 sidecar 不退出(残留 zombie 进程,Task Manager 看到 chariot-sidecar.exe)

### S.10 ⏳ Tauri 前端切到 invoke / event listen

**目标**:`packages/app/` 数据访问层全部从 `fetch("/admin/...")` + SSE 切到
Tauri `invoke()` + `event.listen()`,且消费 Claude 形态 ChatEvent。

- `packages/app/src/lib/api.ts`:
  - 撤所有 `fetch(...)` / endpoint URL
  - 加 `rpc<T>(method: string, params: any): Promise<T>` —— wrap `Tauri.invoke("rpc",
    { method, params })`
  - 各 API 函数(`listConversations` / `getConversation` / `addModel` / 等)直接
    `rpc("...")` 调
- `packages/app/src/lib/streams.ts`:
  - 撤 SSE 解析(原本读 `/v1/messages` SSE)
  - 改:启动 chat = `rpc("chat", req)` + `event.listen("chat_event", handler)`;
    chat_event payload 是 Claude 形态 ChatEvent,直接 dispatch:
    - `kind === "content_block_delta" && delta.type === "text_delta"` → 拼字
    - `kind === "content_block_stop"` 对应 tool_use → 关工具卡片
    - `kind === "tool_result"` → 显示工具结果
    - `kind === "message_stop"` → 一轮收尾
    - `kind === "error"` → 显示 `error_type` / `error_message`
    - `kind === "stream_done"` → 整个 chat 收尾
- 各 page(`pages/Chat.tsx` / `Models.tsx` / `Tools.tsx` / `Conversations.tsx`
  / `Logs.tsx`)调用层适配
- 撤 endpoint.json 相关读取代码(原前端启动时读 sidecar 的 URL)

**验收**:

- **单测**:`packages/app/` 既有 vitest case 全绿;`api.ts` mock 改成 mock
  `Tauri.invoke`(`@tauri-apps/api/core` mock)
- **静态**:`bun run --filter=@chariot/desktop tsc --noEmit` 0 错误;前端 lint
  (若有)0 警告
- **手测**(`tauri dev` 全功能 e2e):
  - **Chat**:发"hi" → 5 秒内看到 streaming text;发 "list /tmp" 触发
    `list_dir` → 工具卡片实时 append;发完 conversation 出现在侧栏
  - **Models**:加 mock model → 列表立即刷新;Probe 跑通(出 latency)
  - **Tools**:点 enable / disable → 状态持久化(关 app 重开仍在)
  - **Conversations**:rename → 标题改;delete → 列表更新
  - **Logs**:Logs 页能看到刚才 chat 的 turn / tool 事件
- **回归**:
  - `grep -rn "fetch.*v1/messages\|fetch.*admin\|EventSource" packages/app/src/` = 0
  - `grep -rn "endpoint.json" packages/desktop/` = 0
- **不通过特征**:
  - UI 卡住 / event 不来(`event.listen` 没挂上)
  - 残留 SSE / fetch 代码导致 build 时打入 dead code
  - chat_event payload 字段名跟后端 ChatEvent 不一致(蛇形 vs 驼峰漏适配)
  - 前端 dispatch 用了 chariot 旧命名(`text` / `tool_use_done` 等),应是
    Claude 形态

### S.11 ⏳ 撤 `chariot/server/` 整目录 + fastapi 依赖

**目标**:所有 surface 切完后,撤掉 server 老代码 + fastapi / uvicorn 依赖。

- `git rm -r chariot/server/agent.py chariot/server/app.py chariot/server/__main__.py`
- `git rm -r chariot/server/controller/`
- `git rm -r chariot/server/runtime/`
- `git rm -r chariot/server/model/`(撤 deprecated 的 Model 接口 + 实现 + registry;
  其实现已被 `chariot/providers/` 完全覆盖)
- `git rm chariot/server/service/exceptions.py`(已迁 agent/exceptions.py)
- `git rm -r chariot/server/service/`(剩余文件已在 S.4 / S.6 全部迁出)
- `git rm -r chariot/server/`(空目录确认无残留再删根)
- `pyproject.toml`:dependencies 删 `fastapi` + `uvicorn`(留 `httpx` /
  `sqlalchemy[asyncio]` / `aiosqlite` / `typer` / `python-ulid`)
- `uv lock` 刷新 + `uv sync`
- 撤 `tests/server/` 整目录(若 S.4 迁移后还残留任何文件,一并清)

**验收**:

- **单测**:`pytest -q` 全套绿;case 数 = S.10 时减去 server/ 测试条数(精确等差)
- **静态**:三件套全绿;`uv lock --check` 通过
- **手测**:
  - `uv run python -m chariot.server` → `ModuleNotFoundError: No module named
    'chariot.server'`(预期,server 已撤)
  - `uv run chariot chat "hello"` 仍工作
  - `bun run --filter=@chariot/desktop tauri dev` 全功能再 e2e 一次(同 S.10
    清单)
- **回归**(grep 守门):
  - `grep -rn "fastapi\|uvicorn\|FastAPI" chariot/ tests/` = 0
  - `grep -rn "from chariot.server\|import chariot.server" chariot/ tests/ packages/` = 0
  - `packages/desktop/src-tauri/binaries/chariot-server.exe` 不存在
- **不通过特征**:
  - 任何 surface(CLI / Tauri)的功能回归
  - pyright 报漏 import / `pyproject.toml` 还引用 fastapi
  - `uv.lock` 里仍含 fastapi / uvicorn

### S.12 ⏳ docs / README / CLAUDE.md 收尾 + 版本号

**目标**:文档 / 入口说明 / 协作约定全面对齐 0.6.0。

- `docs/DESIGN.md` 实施过程偏差校正(若 S.1-S.11 中改了原设计,补在对应章节)
- `docs/FEATURE.md` 全部步骤标 ✅
- `docs/ROADMAP.md`:0.6.0 标完成;0.7.0 (OpenAIProvider + Memory + Skills) /
  0.8.0 (Gateways + 自我进化) / 0.9.0 (Cron + 多 AIAgent + LocalLlama) /
  0.10.0+ (TUI / ACP / MCP) 描述细化
- `README.md` 大改:
  - 标题副标改"自演化 CLI agent"(撤"本机跑的智能体 server")
  - Quick start:撤"起 server + CLI 调 server"两进程示例;改成"`chariot chat`
    一条命令"
  - Architecture 图重画(库化 + multi-surface + 共享 DB)
  - 撤 "OpenAI 客户端怎么接" 段(协议代理已退役,`/v1/messages` 不存在)
  - 撤 "Tech stack" 表里 fastapi / uvicorn
  - 加 stdio JSON-RPC sidecar 通信说明(Tauri 用)
  - 加 Claude 形态 IR 说明(`ChatRequest` / `ChatEvent` / `messages.content`
    三层跟 Claude API 1:1)
- `CLAUDE.md`:
  - "已知敏感点" 撤 endpoint.json / watcher / spawn.lock 三条
  - 加新条:sidecar 进程 stdin 关闭 = 自然退出(不要主动 kill)
  - 加新条:Claude 形态 IR(ChatRequest / ChatEvent / messages 落库 三层 1:1
    跟 Claude API,翻译只在非 Claude Provider 内部)
  - "技术栈" 表撤 FastAPI;加"AIAgent 库化 / stdio JSON-RPC sidecar / RPC
    框架共享"
- 版本号:`pyproject.toml` `version = "0.6.0"` + `chariot/__init__.py`
  `__version__ = "0.6.0"` + 顶部 docstring 改"自演化 CLI agent"

**验收**:

- **单测**:不涉及代码改动,`pytest -q` 全套绿(纯 docs commit 的回归)
- **静态**:三件套全绿(docs 不在 ruff / pyright 范围,但任何被改的 .py 文件
  仍要过)
- **手测**:
  - `uv run chariot --version` → `0.6.0`
  - `uv run chariot chat "hello"` 跑通
  - `bun run --filter=@chariot/desktop tauri dev` 全功能再 e2e 一次(关键路径)
  - README 读一遍,Quick start 拷出来能跑
- **回归**(grep 守门):
  - `grep -rn "fastapi\|/v1/messages\|endpoint.json\|chariot-server\|tui_gateway" docs/ README.md CLAUDE.md` = 0(历史 archive 除外)
  - `chariot/__init__.py` / `pyproject.toml` / `packages/app/package.json` 三处
    版本号一致 = 0.6.0
- **不通过特征**:
  - 文档里仍写 fastapi / `/v1/messages` / endpoint.json / `chariot-server` /
    `tui_gateway`(历史 archive 不算)
  - 版本号三处不一致

---

## 0.7.0+ 路标

详见 [`ROADMAP.md`](ROADMAP.md)。

- **0.7.0** OpenAIProvider + Memory + Skills(自演化基础)
- **0.8.0** Gateways(Telegram + Discord) + 自我进化循环
- **0.9.0** Cron 调度 + 多 AIAgent 实例 + LocalLlamaProvider
- **0.10.0+** TUI 升级 / ACP / MCP / 剩余 Gateway 平台 / Subagent 派生
- **0.11.0+** Plugins 系统(`tools/external/` / `providers/external/` /
  `gateways/external/`)

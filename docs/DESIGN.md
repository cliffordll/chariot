# Chariot 架构设计(0.4.0)

> **当前版本**:`0.4.0`(开发中)
> **上一版归档**:[`docs/history/0.3.1/DESIGN.md`](history/0.3.1/DESIGN.md)
>
> **0.4.0 主题**:Agent 进化第一步 —— **多轮对话记忆 + 工具调用**。
>
> **关键变更**(vs 0.3.1):
> - **新增 `Conversation` 层**:Agent 持有持久化的多轮对话状态;client 通过
>   HTTP header `X-Chariot-Conversation: <id>` 携带会话 ID;不带 header 仍走
>   0.3.1 的 stateless 单轮行为(完全向后兼容)
> - **消息存储按 Anthropic 协议 message 切行**(对齐 Claude Code transcript 风格):
>   `messages` 表 `role ∈ {user, assistant}`(协议原生两种),`content` 列原样
>   存 anthropic content(字符串或 blocks 数组);`tool_use` 嵌在 assistant.content
>   blocks 里,`tool_result` 嵌在 user.content blocks 里。SELECT + reshape 即直接
>   构成 Anthropic 协议 messages 数组,零翻译成本
> - **新增 `Tool` 层**:`Tool` ABC + `ToolRegistry`(类比 `ModelRegistry`)+
>   `tools` 表(`name / type / enabled / options`,默认 4 条 seed 全 disabled)。
>   内置 4 个工具:`read_file` / `list_dir` / `shell_exec` / `http_get`
> - **Agent 工具循环**:`Agent.handle` 内部 while 循环 —— detect tool_use →
>   exec tool → persist tool_result → 拼新 turn 再 call Model → 直到 Model
>   不再返回 tool_use 或达 `max_iter`;Model 契约保持不变(无状态、不感知工具)
> - **会话不绑定 model**:跟 Anthropic 协议一致,`body.model` 每轮必传;
>   `conversations.last_model` 仅作 GUI 派生展示字段
> - **migration v4**:加 `conversations / messages / tools` 三表 + `tools`
>   seed 4 条 disabled 默认行
> - **管理面新端点**:`/admin/conversations/*` + `/admin/tools/*` CRUD
> - **GUI 新增**:Chat 页左侧会话侧栏(从 localStorage 单 entry 扩成持久会话
>   列表)+ Tools 页(类比 Models 页)
>
> **0.3.1 关键变更**(沿用,见归档):路由模型重构(active 概念退役 / `body.model` 路由 / `models.params` runtime sampling)。

---

## 1. 一句话总结

**Chariot = 本机 HTTP server + Anthropic Messages 协议 + 按 entry-name 路由的 Model 网关 + 持久化对话 + Agent 工具调用循环**

- 对外仍只接 `POST /v1/messages`(Anthropic 原生,Anthropic 协议没有 server 端
  会话概念,chariot 通过可选 HTTP header 注入)
- 内部 Model 实现按 DB 里 entries 注入(0.3.1 路由模型重构沿用)
- **新增**:client 可选传 `X-Chariot-Conversation: <id>`,Agent 自动 load 历史 +
  append 新轮 + 持久化;不传 header → 完全等价 0.3.1 stateless 行为
- **新增**:Agent 检测 Model 返回的 `tool_use`,在 server 侧执行工具,把
  `tool_result` 拼回 messages 再调 Model,直到收敛

---

## 2. 分层

```
客户端 (Anthropic SDK / claude code / 自定义)
        │  POST /v1/messages   body.model = <entry-name>
        │  X-Chariot-Conversation: <id>   (可选)
        ▼
Controller (controller/dataplane.py) — 仅读 body + header,转给 Agent
        │
        ▼
Agent (server/agent.py) 类级单例
   持有 _models: dict[str, Model]                  (0.3.1)
   持有 _conversations: ConversationRepo           (0.4.0,注入)
   持有 _tools: dict[str, Tool]                    (0.4.0)
        │
        │  if conversation_id:
        │    history = ConversationRepo.load_messages(id)
        │    body.messages = history + body.messages
        │  按 body.model lookup model
        │
        ▼  while iter < max_iter:
Model Protocol (server/model/base.py)
   └── MockModel / AnthropicModel / ...    # 仍然 stateless、不感知工具
        │
        │  resp = await model.respond(body, stream=False)   # 工具循环时强制非流
        │  ConversationRepo.append(role='assistant', content=resp.content)
        │  if no tool_use in resp.content: break  → 最后一 turn 按客户端要求 stream
        │  exec all tool_use blocks → 拼成 user.content 含 tool_result blocks
        │  ConversationRepo.append(role='user', content=[tool_result, ...])
        │  body.messages += [assistant_with_tu, user_with_tr]
        │
        ▼
Tool ABC (server/tool/base.py)
   └── ReadFileTool / ListDirTool / ShellExecTool / HttpGetTool
       (从 tools.options 读各自配置)

Lifespan startup:
   init_db() → migrations(含 v4:加 conversations / messages / tools)
              ↓
   ModelRepo.seed_if_empty(s)         # 0.3.1 沿用
   ToolRepo.seed_if_empty(s)          # 0.4.0:写 4 条默认 disabled tool 行
   ModelRepo.list_entries(s) → ChariotConfig
   ToolRepo.list_enabled(s)  → ToolConfig
              ↓
   Agent.install_from_config(model_config, tool_config, conversation_repo)
```

`Agent` 与 0.3.1 的本质区别:从"持有 name → Model 字典 + 单次 respond 即返回"
变成"持有 Model + Conversation + Tool 三套状态,handle 是工具循环"。

---

## 3. 唯一对外端点

`POST /v1/messages`,Anthropic Messages 协议。

**0.4.0 新约束 / 沿用约束**:

- `body.model` 每轮必传(沿用 0.3.1 unknown_model_name 校验)
- 可选 header `X-Chariot-Conversation: <id>`:
  - **不传**:stateless 单轮(完全等同 0.3.1 行为,messages 表不写)
  - **传且 conv 不存在**:server 自动创建该 id 的 conversation,首轮 init
  - **传且 conv 存在**:server load 历史 messages,prepend 到 body.messages 前
    再调 Model
- conversation id 形态:**ULID 推荐**(client 生成或 server 生成都接受);
  正则 `^[0-9A-Z]{26}$` 校验失败 → 400 `invalid_conversation_id`
- 工具调用循环过程中**只有最后一 turn 按客户端要求 stream**;中间 turn 强制非流
  (简化设计,客户端首字延迟 = 全部工具 + 中间 LLM 调用之和,0.4.0 接受这个延迟)

SSE 事件序列:沿用 0.3.1。最后一 turn 的 stream 形态对客户端完全透明。

---

## 3.1 Conversation ID 获取流程

`X-Chariot-Conversation` header 的值从哪来 —— 三种模式,由 client 决定:

| 模式 | 谁生成 ID | 创建时机 | 适用场景 |
|---|---|---|---|
| **A. Client 生成 ULID** | client | 首次发 `/v1/messages` 带 header,server `ensure_exists` 时 INSERT | 客户端有 ULID 库 / 想零额外往返 |
| **B. Server 生成,显式创建** | server | 先调 `POST /admin/conversations` → 拿 `id` → 再发 `/v1/messages` | GUI 点 [+ New] 后会话需立即出现在侧栏列表 |
| **C. 不传 header** | — | 不创建会话 | 完全等价 0.3.1 stateless 行为(向后兼容关键点)|

### 各 client 推荐用法

| Client | 模式 | 理由 |
|---|---|---|
| Chat 页 GUI(`packages/app`)用户点 [+ New] | **B** | 立即在左侧侧栏可见;GUI 不依赖 ULID 库 |
| CLI `chariot chat`(默认) | **A** | 启动时 `python-ulid` 生成,无网络往返 |
| CLI `chariot chat --no-history` | **C** | 显式 stateless |
| CLI `chariot chat --conversation <id>` | 接续已有 | 不生成,用 client 给的 id |
| 第三方代码直接打 `/v1/messages` | **A 或 C** | 用户自己决定 |
| claude code / 透传 Anthropic SDK | **C** | 这些 SDK 不知道 conversation 概念;0.4.0 **不强制**它们带 header |

### 模式 A:auto-create 流程

```http
POST /v1/messages
X-Chariot-Conversation: 01JD7K8YQXM2N8R5VF3PCWE4ZB
Content-Type: application/json

{"model": "mock", "messages": [...], ...}
```

server 行为:

1. 正则校验 `^[0-9A-Z]{26}$`,失败 → 400 `invalid_conversation_id`
2. `ConversationRepo.ensure_exists(id)`:不存在则 INSERT(`title=NULL` / `last_model=NULL`);存在则 no-op
3. 走正常 load history + handle 流程

### 模式 B:显式创建流程

```http
POST /admin/conversations
Content-Type: application/json

{"title": "可选,不传也行"}
```

```http
HTTP/1.1 200 OK
{
  "id": "01JD7K8YQXM2N8R5VF3PCWE4ZB",
  "title": null,
  "last_model": null,
  "created_at": "...",
  "updated_at": "..."
}
```

server 用 `python-ulid` 生成 id,DB INSERT 后返回完整对象。后续 client 在
`X-Chariot-Conversation` header 携带这个 id。

### ID 严格性

**严格 ULID 校验**(`^[0-9A-Z]{26}$`),不接受任意人类可读字符串作为 id。理由:

- ULID 单调时间戳前缀让 `ORDER BY id` 即时间序,GUI 列表查询零成本
- 生成端唯一性几乎免费(碰撞概率 1e-30)
- 接受任意字符串会给 0.5+ 的"搜索 / 排序 / 索引"加复杂度,目前没真实承载场景

依赖:server 用 `python-ulid` 生成,client 各自语言生态都有 ULID 实现
(`ulid-py` / `ulid` npm / `oklog/ulid` Go 等)。

---

## 4-5. 数据面流程 + Model 接口契约(沿用 0.3.1,见归档)

接口契约硬约束(**Model 在 0.4.0 严格不变**):

1. **无状态**;**不碰 DB**;**不感知"上游"**;**不感知工具调用**(工具循环逻辑全在 Agent)
2. `name: str` 属性 + `async respond(body, *, stream) -> Response` 方法
3. 实现 `from_config(options)` classmethod;`ModelRegistry.register("type", ModelClass)` 显式注册

工具调用对 Model 完全透明 —— Model 看到的就是"messages 数组里多了 assistant
含 tool_use 的轮 + user 含 tool_result 的轮",这是 Anthropic Messages 协议原生
形态,Model 不需要任何特殊处理。

---

## 6. 模型管理层(沿用 0.3.1,见归档)

`models` 表 / `ModelRepo` / `ChariotConfig` / `Agent.models` 字典 / `ModelRegistry`
全部沿用,无改动。`AnthropicModel` 实现要点不变。

---

## 7. Conversation 层(0.4.0 新)

### 7.1 数据存储

```sql
-- migration v4 (a)
CREATE TABLE conversations (
    id               TEXT PRIMARY KEY,            -- ULID;client 生成或 server 生成
    title            TEXT,                        -- 可选;空则 GUI 从首条 user msg 截取展示
    last_model  TEXT,                        -- 派生字段:每写一轮 assistant msg 同步
                                                  -- 仅供 GUI 侧栏展示"最近用的什么 model"
                                                  -- 不影响协议(每轮 body.model 仍然必传)
    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_conversations_updated ON conversations(updated_at DESC);

-- migration v4 (b)
CREATE TABLE messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id  TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    seq              INTEGER NOT NULL,            -- 会话内单调,0 起
    role             TEXT NOT NULL,               -- 'user' | 'assistant'(Anthropic 协议原生两种)
    content          TEXT NOT NULL,               -- JSON;原样存 anthropic content
                                                  -- 字符串(纯文本)或 blocks 数组
                                                  -- (含 text / tool_use / tool_result blocks)
    model_name       TEXT,                        -- 仅 role='assistant' 行非空,记本轮用的 entry name
    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(conversation_id, seq)
);
CREATE INDEX idx_messages_conv ON messages(conversation_id, seq);
```

**为什么按 message 切行而非整个 messages 数组单行存**:

- 跟 Anthropic 自家 Claude Code transcript 同构:每条 message 一行,content
  保留协议原生形态(`tool_use` 嵌在 assistant.content blocks、`tool_result`
  嵌在 user.content blocks),`SELECT * ORDER BY seq` 直接构成 Anthropic
  messages 数组,零翻译成本 —— Agent / GUI / admin 端点共用同一形态
- 单行存整个数组的退化方案:每轮要 read-decode-mutate-encode-write 整个 JSON,
  并发写难、单条修改 / cascade 删除 / 分页都要绕开 JSON,不值得
- **不抽**"tool_use / tool_result 各自独立行"的更细粒度形态(B-2):那样要写
  reshape 函数把连续 assistant_text + tool_use 合回 anthropic content blocks,
  增加翻译代码 + 测试覆盖,而 SQL 查询便利度 / GUI 渲染都没有显著收益。
  CLAUDE.md ⭐ #4 内聚优先于 DRY:跟协议 1:1 对齐才是最内聚的形态

### 7.2 类设计

| 类 | 文件 | 职责 |
|---|---|---|
| `ConversationRow` ORM | `chariot/server/database/models.py` | 映射 conversations 表 |
| `MessageRow` ORM | 同上 | 映射 messages 表 |
| `ConversationRepo` | `chariot/server/repository/conversation_repo.py` | CRUD:create / get / list / delete / update_title;`load_messages_as_anthropic(id) -> list[dict]`(SELECT 后 reshape 成 `[{role, content}, ...]` —— content 已经是协议原生形态,reshape 仅丢掉 `seq / model_name` 等元数据列);`append_message(conv_id, role, content, model_name=None)` |
| `Conversation` | `chariot/server/agent_state.py` (或 inlined Agent) | in-memory 形态(可选,Agent 可以直接使用 Repo 不抽 Conversation 对象) |

**抽不抽 `Conversation` 对象的取舍**:0.4.0 不抽。Agent 直接用 ConversationRepo
读写,一个会话的"运行时状态"完全由 DB 行承载。抽 Conversation 类目前没有承载
的额外行为(没有缓存 / 锁 / 派生计算),抽出来就是空壳 —— 反 CLAUDE.md ⭐ #5。
将来若要加"会话级 token 计数缓存""并发写锁"等再抽。

### 7.3 Agent 接入

```python
class Agent:
    _models: dict[str, Model] = {}                # 0.3.1
    _tools: dict[str, Tool] = {}                  # 0.4.0
    _conversation_repo: ConversationRepo | None = None  # 0.4.0(实际从 session 拿,见下)

    @classmethod
    def install_from_config(
        cls,
        model_config: ChariotConfig,
        tool_config: ToolConfig,
    ) -> Agent:
        ...

    async def handle(
        self,
        body: dict,
        *,
        conversation_id: str | None = None,
        stream: bool,
        session: AsyncSession,                    # 0.4.0:DB session 透传
    ) -> Response:
        ...
```

**ConversationRepo 不挂在 Agent 上、每次 handle 现 new**:Repo 持有 session,
session 是 request-scoped(FastAPI Depends),不能跨请求复用。Agent 自己是单例,
持 ConversationRepo 实例会绑死 session,违反"Repo 跟 session 同生命周期"。
所以 Agent.handle 接收 session 参数,内部实例化 `ConversationRepo(session)`。

### 7.4 工具调用循环(handle 主流程)

```python
async def handle(self, body, *, conversation_id, stream, session):
    conv_repo = ConversationRepo(session)
    model = self._lookup_model(body)             # 0.3.1 路由

    # load + prepend 历史
    if conversation_id:
        await conv_repo.ensure_exists(conversation_id)
        history = await conv_repo.load_messages_as_anthropic(conversation_id)
        body['messages'] = history + body.get('messages', [])
        # persist 本轮新增的 user message(只 persist 客户端这次发的,不重复持久化历史)
        for msg in incoming_user_messages:
            await conv_repo.append_message(conversation_id, 'user', msg)

    # 工具循环
    iter_count = 0
    while iter_count < MAX_TOOL_ITER:
        iter_count += 1
        is_last_iter_unknown = True              # 不知道这一轮是不是最后一轮,所以强制非流
        resp = await model.respond(body, stream=False)

        if conversation_id:
            await conv_repo.append_message(
                conversation_id, 'assistant', resp.content,
                model_name=body['model'],
            )

        tool_uses = extract_tool_use_blocks(resp.content)
        if not tool_uses:
            # 收敛:这就是最后一轮,按客户端要求重发 stream
            if stream:
                # 重新调一次 model 拿 stream 形态(简化:不缓存,重发一次)
                # 取舍:多调一次 LLM,但实现简单,0.4.0 可接受
                return await model.respond(body, stream=True)
            return resp

        # 执行所有 tool_use,拼下一轮 body
        tool_result_blocks = []
        for tu in tool_uses:
            result_block = await self._execute_tool(tu)   # → {type: 'tool_result', ...}
            tool_result_blocks.append(result_block)

        # tool_results 按 Anthropic 协议必须打包成一条 user message 持久化
        if conversation_id:
            await conv_repo.append_message(
                conversation_id, 'user', tool_result_blocks,
            )
        body['messages'].append({'role': 'assistant', 'content': resp.content})
        body['messages'].append({'role': 'user', 'content': tool_result_blocks})

    raise ServiceError('tool_iter_exceeded', f'>{MAX_TOOL_ITER} iterations')
```

**关键取舍**:

- **MAX_TOOL_ITER**:默认 10,可配置(env `CHARIOT_MAX_TOOL_ITER`)。超限抛 400
- **最后一轮重发拿 stream**:简单实现 —— 工具循环全程 `stream=False`,确认收敛
  后再单独调一次 stream。多花一次 LLM 调用换实现简洁;0.5+ 可优化为"流式
  tool_use 检测"
- **history persist 时机**:incoming user msg 在 load history 之后立刻 persist,
  保证就算工具循环中途出错,user 这条已经记下;assistant / tool_result 在产生
  时立即 persist,中断也不丢上下文

---

## 8. Tool 层(0.4.0 新)

### 8.1 数据存储

```sql
-- migration v4 (c)
CREATE TABLE tools (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    type        TEXT NOT NULL,                   -- 跟 name 同形;预留同类多实例
    enabled     INTEGER NOT NULL DEFAULT 0,      -- 0/1
    options     TEXT NOT NULL DEFAULT '{}',      -- JSON;workdir / allowed_domains 等
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_tools_name ON tools(name);
```

`ToolRepo.seed_if_empty(s)` 写 4 条:

| name | type | enabled | options 默认 |
|---|---|---|---|
| `read_file` | `read_file` | 0 | `{"max_bytes": 1048576}` |
| `list_dir` | `list_dir` | 0 | `{}` |
| `shell_exec` | `shell_exec` | 0 | `{"workdir": "~/.chariot/sandbox", "timeout_s": 30}` |
| `http_get` | `http_get` | 0 | `{"allowed_domains": [], "max_bytes": 524288}` |

**全部默认 disabled** —— 用户必须显式打开,符合"安全优先"。

### 8.2 类设计

| 类 | 文件 | 职责 |
|---|---|---|
| `Tool` ABC | `chariot/server/tool/base.py` | 抽象基类,见 §8.3 契约 |
| `ToolRegistry` | `chariot/server/tool/registry.py` | 类比 ModelRegistry:`register(type, ToolClass)` / `build(entry) -> Tool` / `known_types()` |
| `ReadFileTool` / `ListDirTool` / `ShellExecTool` / `HttpGetTool` | `chariot/server/tool/{readfile,listdir,shellexec,httpget}.py` | 4 个内置实现 |
| `ToolEntry` | `chariot/server/config.py` | 数据形态 = name / type / enabled / options |
| `ToolConfig` | 同上 | 纯 entries 容器,classmethod `from_db(session)` 装载入口 |
| `ToolRepo` | `chariot/server/repository/tool_repo.py` | 类比 ModelRepo:CRUD + `list_enabled(s)` + `seed_if_empty(s)` |

模式跟 Model 层完全同构 —— 0.4.0 引入 Tool 层最大的设计美德是它跟 0.3.1 已稳的
Model 层是镜像 fold,DESIGN / FEATURE / 测试结构都直接 copy-paste 改名词。

### 8.3 Tool 接口契约

```python
class Tool(ABC):
    name: str                                    # 实例属性,跟 ToolEntry.name 一致

    @classmethod
    @abstractmethod
    def from_config(cls, entry: ToolEntry) -> Tool:
        """从 ToolEntry 建实例(读 entry.options 解析自己的配置)"""

    @abstractmethod
    def schema(self) -> dict:
        """返回 Anthropic tool definition JSON
        ({"name": ..., "description": ..., "input_schema": {...}}),
        Agent 把所有 enabled tools 的 schema 收集后,在调 Model 前
        塞进 body.tools 数组(沿用 Anthropic 原生 tool definition 协议)"""

    @abstractmethod
    async def execute(self, input: dict) -> dict:
        """执行一次工具调用,返回 anthropic tool_result content
        ({type: 'tool_result', content: [...], is_error?: bool})"""
```

**契约硬约束**(类比 Model):

1. **无状态**(每次 execute 独立)—— execute 之间不共享内存,失败不影响下一次
2. **不碰 DB**(不写 messages 表,持久化由 Agent 负责)
3. **不感知 conversation_id**(同 Model)
4. 实现 `from_config` classmethod;在 `chariot/server/tool/__init__.py`
   显式调用 `ToolRegistry.register("type", ToolClass)`(非装饰器副作用)

### 8.4 内置工具实现要点

| 工具 | 输入 schema | 实现要点 |
|---|---|---|
| `read_file` | `{path: str}` | 限 `max_bytes` 截断;路径绝对化、检查存在;返回文件内容字符串(text 类) + UTF-8 解码失败时 base64 |
| `list_dir` | `{path: str, recursive?: bool}` | 默认非递归;返回 `[{name, type: 'file'\|'dir', size}]` JSON |
| `shell_exec` | `{cmd: str, args?: list[str]}` | 在 `options.workdir` 下跑(目录不存在则 mkdir);`timeout_s` 限时;**Windows 下用 PowerShell,Unix 下用 bash**;stdout / stderr / exit_code 都返回 |
| `http_get` | `{url: str, headers?: dict}` | URL 解析后域必须在 `options.allowed_domains` 白名单(子串 / 子域名匹配语义详见实现);`max_bytes` 限响应大小;返回 `{status, body, headers}` |

`shell_exec` 沙盒目录策略:

- `~/.chariot/sandbox/` 作为根
- **不**按 conversation_id 切子目录(0.4.0 简化;若需要,Agent 在 execute 前注入
  `effective_workdir`)
- 用户清理沙盒方式:CLI `chariot tool sandbox clean`(0.4.0 选做,先靠手动 rm)

`http_get` 域白名单匹配规则(0.4.0 简化版):

- 白名单存"允许的 host 后缀"列表,如 `["api.github.com", "duckduckgo.com"]`
- 请求 URL 的 host 完全等于白名单某项 → 通过
- 暂不支持子域通配(`*.example.com`),0.4.x 看需求再加
- 白名单为空 → 所有请求拒绝(等价工具禁用)

---

## 9. 表清单

```sql
-- 0.1.0
CREATE TABLE logs ( ... );

-- 0.3.0 / 0.3.1
CREATE TABLE models (
    ...,
    options TEXT NOT NULL,
    params  TEXT NOT NULL DEFAULT '{}'
);

-- 0.4.0 新增
CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    title TEXT,
    last_model TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    model_name TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(conversation_id, seq)
);

CREATE TABLE tools (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    options TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

migrations:`v0`(空)→ `v1`(logs)→ `v2`(0.3.0:models / settings)→
`v3`(0.3.1:`ALTER models ADD COLUMN params` + `DROP TABLE settings`)→
`v4`(0.4.0:加 conversations / messages / tools 三表 + tools seed 4 条 disabled)。
`PRAGMA user_version` 沿用 0.1.0 的迁移机制。

---

## 10. 单例 / lifespan

```python
async with lifespan(_app):
    await init_db()                                           # 含 v4 migration
    async with session_maker() as s:
        await ModelRepo(s).seed_if_empty()
        await ToolRepo(s).seed_if_empty()                     # 0.4.0 新增
        model_config = await ChariotConfig.from_db(s)
        tool_config = await ToolConfig.from_db(s)             # 0.4.0 新增
    Agent.install_from_config(model_config, tool_config)
    yield
    Agent.uninstall()
    await dispose_db()
```

`ConversationRepo` 不进 lifespan —— request-scoped,Agent.handle 现 new。

---

## 11. AnthropicModel(沿用 0.3.1,见归档)

`options` key / API key 来源 / 错误映射 / 流式中途断开行为均不变。

工具调用对 AnthropicModel 透明 —— body.tools 在 controller / Agent 层注入,
AnthropicModel 透传上游即可。Anthropic 原生支持 tool_use,无需 chariot 适配。

---

## 12. 管理面

```
GET  /admin/ping       → {ok: true}
GET  /admin/status     → {version, uptime_ms, entries_count, tools_enabled, conversations_count, url}
                                                  # 0.4.0:加 tools_enabled / conversations_count
POST /admin/shutdown   → graceful shutdown
GET  /admin/logs       → list LogOut(limit / offset / since)
GET  /admin/stats      → {period, total_requests, success_rate, avg_latency_ms}

# Models(沿用 0.3.1)
GET  /admin/models                       → {available, types, entries: [...]}
POST /admin/models/{name}/probe
POST   /admin/models/entries
PUT    /admin/models/entries/{name}
DELETE /admin/models/entries/{name}
POST   /admin/models/entries/{name}/duplicate

# Tools(0.4.0 新)
GET  /admin/tools                        → {types, entries: [{name, type, enabled, options}]}
PUT  /admin/tools/{name}                 → 改 enabled / options(name 不变,不允许 add/remove)
                                          # tools 表是 4 条 seeded fixtures,不开放 CRUD,只改配置

# Conversations(0.4.0 新)
GET    /admin/conversations              → list({id, title, last_model, message_count, created_at, updated_at})
                                            支持 limit / offset / order_by=updated_at|created_at
GET    /admin/conversations/{id}         → detail + 全部 messages
DELETE /admin/conversations/{id}         → cascade 删消息
PATCH  /admin/conversations/{id}         → 改 title
POST   /admin/conversations              → 显式创建(返回 id);非必需 —— /v1/messages 带未存在的
                                            X-Chariot-Conversation 也会自动创建
```

**为什么 tools 不开 CRUD**:0.4.0 只支持 4 条内置工具,数量固定,加新 tool type
要发新版本(在 ToolRegistry 注册)。开放 CRUD 反而要处理"用户加的 entry name
对应不到 type"等边界情况,价值不大。0.4.x 若加"同类工具多实例"(比如两个
http_get 用不同白名单)再开 POST。

---

## 13. SDK / CLI 增量

### SDK(`chariot/sdk/`)

```python
class ProxyClient:
    # 0.4.0 新方法
    async def list_conversations(self, *, limit=50, offset=0) -> ConversationsListResponse
    async def get_conversation(self, conv_id: str) -> ConversationDetailResponse
    async def delete_conversation(self, conv_id: str) -> None
    async def update_conversation(self, conv_id: str, *, title: str) -> None

    async def list_tools(self) -> ToolsListResponse
    async def update_tool(self, name: str, *, enabled: bool | None, options: dict | None) -> None
```

### CLI(`chariot/cli/`)

```
chariot conversation list [--limit N]
chariot conversation show <id>
chariot conversation rm <id>
chariot conversation rename <id> <new-title>

chariot tool list
chariot tool enable <name>
chariot tool disable <name>
chariot tool config <name> -o key=value [-o ...]
```

### Chat 客户端侧(`chariot chat`)

CLI Chat 命令加 `--conversation <id>` 参数,默认每次 new(传新 ULID),设了 id
就接续会话。Chat 页(GUI)同理,加左侧会话侧栏。

---

## 14. 前端管理面

### Chat 页(`packages/app/src/pages/Chat.tsx`)

- **左侧加 ConversationList 侧栏**:列出所有 conversations(按 updated_at desc),
  点击切换 active conversation;[+ New] 按钮起新会话;每行右端 [⋯] 改名 / 删
- **active conversation state 改为 `{id, messages[]}`**(从 server 拉,不再纯
  localStorage);0.3.1 的 `chariot.chat.selected_entry` 仍保留(每会话独立选 entry)
- 发请求时:body.model 取自当前 entry.params 的 model 字段,header 加
  `X-Chariot-Conversation: <active.id>`
- 工具调用过程对用户可见:assistant 含 tool_use 的轮渲染为带工具图标的卡片,
  tool_result 渲染为可折叠区(默认折叠,点击展开看输出)

### Tools 页(新)

类比 Models 页,4 行(只读 name / type / [启/停]开关 + 行展开 ParamsEditor 编
options)。

---

## 15. 设计模式 + 测试覆盖

### 设计模式新增(0.4.0)

| 模式 | 用在哪 | 解决什么 |
|---|---|---|
| **Repository(Conversation/Tool)** | `ConversationRepo` / `ToolRepo` | 跟 0.3.1 ModelRepo 同模式 |
| **Registry(Tool)** | `ToolRegistry` | 跟 0.3.1 ModelRegistry 同模式 |
| **State machine(Agent 工具循环)** | `Agent.handle` 内部 while 循环 | 多轮 tool_use → exec → tool_result → 重调 Model 直到收敛 |
| **Adapter(content block dispatch)** | Agent 检测 content blocks 时按 type 分支(text / tool_use / tool_result);GUI 渲染同 | 沿用 ⭐ #3:三分支 dispatch 用 ABC + 子类 |

### 测试覆盖

| 文件 | 覆盖 |
|---|---|
| `tests/server/test_conversation_repo.py` | CRUD + load_messages_as_anthropic + cascade delete + seq 单调 |
| `tests/server/test_tool_repo.py` | CRUD + seed_if_empty(4 条) + list_enabled + 校验 |
| `tests/server/test_tools.py` | 4 个内置工具的 execute 路径(临时目录 / mock httpx) |
| `tests/server/test_admin_conversations.py` | conversations 端点 |
| `tests/server/test_admin_tools.py` | tools 端点 |
| `tests/server/test_agent_tool_loop.py` | 工具循环主路径 + max_iter + 失败传播 |
| `tests/server/test_dataplane.py`(扩) | X-Chariot-Conversation header + ULID 校验 + auto-create |

---

## 16. 0.5.0+ 路标

详见 `docs/ROADMAP.md`。要点:

- **0.5.0**:Agent 自我进化循环(读 logs feedback 调权重 / 切 model / 修 prompt)
- **0.6.0**:多 Agent 实例(logs / conversations / tools 加 agent_id 维度)
- **0.7.0?**:工具调用流式优化(中间 turn 也增量 stream;省掉"最后一轮重发")

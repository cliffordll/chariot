# C1: UI/CLI 多对话状态持久化

> 状态: 进行中 | 分支: feat/0.8.8-provider | 责任人: <待分配>

## 功能概述

让 chariot 的 CLI 和 UI 在**切换/接续对话**时,自动恢复到该对话上次使用的 **Provider** 和 **Agent** 配置。

当前问题:
- UI 切换对话时,provider 和 agent 下拉框保持原值,不会自动跟随对话切换。
- CLI 使用 `--conversation <id>` 接续时,需要重新传 `--provider` 和 `--agent`,否则回退到 DB 默认/空值。

目标:
- 对话创建时记录 provider + agent。
- 对话切换/接续时自动恢复 provider + agent。
- CLI flag 优先级 > 对话恢复值 > DB 默认值。

## 设计意图

对话是 chariot 的核心工作单元。每个对话有独立的:
- `provider_name`: 使用哪个 LLM Provider 进行对话。
- `agent_profile`: 使用哪个 Agent 配置(prompt bundle / tool profile / provider profile)。

这两个配置应该绑定到对话本身,而不是绑定到前端状态或 CLI 命令行。这样用户可以在不同对话间自由切换,每个对话保持自己的上下文和配置。

**关键原则:**
- `agent_profile` 优先级高于 `provider_name`。agent 的 `provider_profile` binding 会覆盖 provider 路由。
- `model` 是独立维度,不受 agent_profile 影响(除非 agent 配置里显式指定)。
- **0.8.8+ 工具调用行为变更**:没有配置 toolset(`agent_profile.tool_profile` 为空或 dangling) → **不挂载任何工具**。工具调用必须通过 toolset 显式配置,不再默认挂载全量 enabled 工具。

## 数据模型

### conversations 表扩展

```sql
-- 新增列
ALTER TABLE conversations ADD COLUMN agent_profile TEXT;

-- 已有列(已改名)
-- last_provider: 记录 provider entry name(0.6.0+)。字段名原为 last_model,
--   因实际存的是 entry name(如 "gpt4"、"anthropic")而非模型 ID,故改名。
--   语义:最后一次回 assistant message 时用的 provider entry。
-- agent_profile: 记录 agent profile name(本阶段新增)
```

### 写入时机

- `last_provider`: 每次写 assistant message 时更新(已有逻辑)。
- `agent_profile`: 每次写 assistant message 时,如果 `req.agent_profile` 非空则更新。

### 读取时机

- UI `loadConvDetail(id)`: 返回 `conversation.agent_profile` + `conversation.last_provider`。
- CLI `_run()`: 若 `--conversation <id>` 传入,先查 DB 恢复 provider/agent。

## 配置优先级(生效时)

当多个配置源同时存在时,按以下优先级决定最终生效值:

### Provider 路由
```
agent_profile.provider_profile > CLI --provider > 对话恢复值(last_provider) > DB 默认
```

### Model(模型 ID)
```
CLI --model > entry.options.model > Provider 内置默认
```
- `--model` 只改 wire LLM id,不重建 Provider / ClientSpec
- agent_profile 不覆盖 model(除非 entry 里指定)

### 连接信息(base_url / api_key)
```
CLI --base-url / --api-key > entry.options > 环境变量 > Provider 内置默认
```
- 改连接信息会触发 Provider 重建 + ClientSpec 新建/复用
- 注意:若 agent 把 provider 切到别的 entry,CLI 的 base-url patch **不会跟过去**

### Agent 绑定
```
CLI --agent > 对话恢复值(agent_profile) > 无绑定(走全局 active bundle + 不挂载工具)
```
- 无 agent 绑定 → 不挂载任何工具(0.8.8+ 行为)。

## 已完成部分

### ✅ Prompt 版本去重(0.8.8)

- `PromptRepo.update_bundle()`: 与上一版本内容比较，相同(忽略字段顺序)则不创建新版本。
- `_layers_equal()`: 深度比较 layers，忽略 dict 键顺序和列表项顺序。
- 解决字段顺序变化导致重复版本的问题。

### ✅ OpenAI Provider 支持(0.8.8)

- `chariot/providers/builtin/openai.py`: OpenAI Chat Completions API ↔ Claude ChatEvent 完整翻译。
- 自动补 content_block_start / content_block_stop / message_stop(适配 ProviderEventValidator)。
- 处理第三方兼容 API 重复 role 问题。
- 注册到 `ProviderRegistry`,支持 `chariot provider add --type openai`。

### ✅ Provider 类型校验(0.8.8)

- `ProviderRepo._check_type()`: 未知 type 显式报错,返回已知类型列表。
- CLI 和 Sidecar 统一校验。

### ✅ UI Provider 管理页增强(0.8.8)

- `TYPE_SCHEMAS` 增加 `openai`(model/api_key/api_key_env/base_url)。
- `TEMPLATES` 增加 GPT-4 / GPT-4o 快速预设。
- 修复 `types` 下拉列表为空 bug(从 `status.known_types` 读取)。

### ✅ UI 导航重构(0.8.8)

- 左侧导航栏改为分组折叠结构(Config / Agents / Ops)。
- 分组标题加 `bg-accent/40` 背景,与导航项区分。
- 导航项重排序:Dashboard/Chat(扁平) → Config → Agents → Ops。
- Chat 页 agent 下拉框移到 provider 前面(因 agent_profile 优先级更高)。

### ✅ 工具调用行为变更(0.8.8)

- `_inject_default_tools()`:没有配置 toolset(`agent_profile.tool_profile` 为空或 dangling) → **不挂载任何工具**(`tools=[]`)。
- 工具调用必须通过 `agent_profile.tool_profile` 显式绑定到命名 toolset,不再默认挂载全量 enabled 工具。
- dangling toolset name → fallback 到空工具(不挂载),不再 fallback 到全量。
- 涉及:`chariot/agent/run.py` + `tests/agent/test_agent_profile_binding.py`。

## 待完成部分

### 🔄 DB Migration

- [ ] `conversations` 表增加 `agent_profile` 列。
- [ ] `conversations.last_model` rename to `last_provider`(或保留旧列,新增 last_provider)。
- [ ] `PRAGMA user_version` 升级。

### 🔄 Repo 层

- [ ] `ConversationRepo.create()`: 支持 `agent_profile` 参数。
- [ ] `ConversationRepo.append_message()`: 写 message 时同步更新 `last_provider` + `agent_profile`。
- [ ] `ConversationRepo.get()`: 返回 `agent_profile` + `last_provider`。

### 🔄 Agent 层

- [ ] `AIAgent._run_chat_once()`: 写 assistant message 时传入 `agent_profile`。

### 🔄 Sidecar API

- [ ] `get_conversation` 返回 `agent_profile` + `last_provider`。
- [ ] `Conversation` 序列化包含 `agent_profile` + `last_provider`。

### 🔄 前端 UI

- [ ] `api.ts`: `Conversation` 接口增加 `agent_profile` + `last_provider`。
- [ ] `Chat.tsx`: `loadConvDetail()` 恢复 provider + agent。
  ```typescript
   // 恢复 provider
   const lastMsg = [...messages].reverse().find(m => m.role === "assistant" && m.provider_name);
   if (lastMsg?.provider_name) setSelectedEntry(lastMsg.provider_name);
   else if (conversation.last_provider) setSelectedEntry(conversation.last_provider);
   
   // 恢复 agent
   if (conversation.agent_profile) setSelectedAgent(conversation.agent_profile);
   else setSelectedAgent(null);
  ```
- [ ] UI 布局: **agent 下拉框放在 provider 前面**。
  - 原因:`agent_profile.provider_profile` 优先级高于 `provider_name`;
    先选 agent 后 provider 可自动跟随 agent binding,减少用户困惑。
  - 涉及:`Chat.tsx` 中 `<Select>` JSX 顺序(已调)。

### 🔄 CLI

- [ ] `_run()`: 若 `--conversation <id>`,先查 DB 恢复 provider/agent。
  ```python
  restored_provider, restored_agent = await _restore_conversation_config(conversation_id)
  provider_name = provider or restored_provider or await _resolve_provider_name(None)
  agent_profile = agent_profile or restored_agent
  ```
  - 注意:即使恢复了对话值,若 CLI 传了 `--provider`/`--agent`,仍以 CLI flag 为准。
- [ ] `ChatRepl`: 每轮输入前重新查 conversation 最新配置(防止用户中途在 UI 改了)。

## 验收标准

| # | 场景 | 预期 |
|---|------|------|
| 1 | UI 新建对话,选 provider=gpt4, agent=dev, 发消息 | DB 中 `conversations.last_provider="gpt4"`, `agent_profile="dev"` |
| 2 | UI 切换到别的对话再切回来 | provider 下拉自动切回 gpt4,agent 下拉自动切回 dev |
| 3 | CLI `chariot chat "hi" --provider gpt4 --agent dev --conversation new` | DB 正确写入 provider + agent |
| 4 | CLI `chariot chat "follow up" --conversation <id>` | 自动恢复 provider=gpt4, agent=dev,无需再传 flag |
| 5 | CLI `chariot chat "override" --conversation <id> --provider anthropic` | 使用 anthropic 覆盖对话默认值,但 DB 仍保持原值 |
| 6 | UI 和 CLI 交替使用同一会话 | 配置双向同步,切换时一致 |
| 7 | 选 agent=dev(其 provider_profile=ollama),再手动选 provider=gpt4 | 实际路由到 ollama(agent_profile 优先级更高) |
| 8 | 发消息时不选 agent(无 toolset 绑定) | LLM 看不到任何 tool schema,不触发工具调用 |
| 9 | 选 agent=safe(其 tool_profile=fs_safe,成员 read_file/list_dir) | LLM 只能看到 read_file 和 list_dir 两个工具 |

## 测试覆盖

### 已完成测试

| 模块 | 测试文件 | 覆盖场景 |
|------|----------|----------|
| OpenAI Provider | `tests/providers/builtin/test_openai.py` | text 流、tool_calls、401、500、网络不可达、ConfigError |
| Provider Registry | `tests/providers/test_registry.py` | register/build/known_types |
| Provider CRUD | `tests/cli/test_provider_parse.py` | k=v / JSON / 混合格式解析 |
| UI Provider | `tests/cli/test_commands.py` | provider list/show/add/update/delete/probe |
| Agent Profile Binding | `tests/agent/test_agent_profile_binding.py` | provider_profile 覆盖、prompt_bundle 回退、toolset filter、空 toolset 关闭工具、无 agent 不挂载工具、dangling toolset |
| Agent Default Tools | `tests/agent/test_run.py` | 无 agent 时不挂载工具(0.8.8+ 行为)

### 待补充测试

| 模块 | 测试文件 | 覆盖场景 |
|------|----------|----------|
| Conversation agent_profile | `tests/repos/test_conversation_repo.py` | create/append_message 写入 agent_profile;last_provider 改名 |
| Sidecar get_conversation | `tests/sidecar/test_conversation_methods.py` | 返回 agent_profile + last_provider |
| UI Chat 恢复 | 手工测试 | 切换对话时 provider/agent 自动恢复 |
| CLI 恢复 | `tests/cli/test_chat_commands.py` | `--conversation` 自动继承 provider/agent |
| 覆盖优先级 | `tests/cli/test_chat_commands.py` | CLI flag > 对话恢复 > DB 默认 |
| Agent 覆盖 provider | `tests/cli/test_chat_commands.py` | --agent 的 provider_profile 覆盖 --provider |

## 相关文件

### 后端
- `chariot/database/models.py` — ConversationRow schema(last_provider / agent_profile)
- `chariot/repos/conversation_repo.py` — create/append_message/get
- `chariot/agent/run.py` — _run_chat_once 写 message + _resolve_binding + _inject_default_tools(工具挂载)
- `chariot/sidecar/methods/conversation.py` — get 返回 agent_profile + last_provider
- `chariot/sidecar/services/conversation.py` — serialize

### 前端
- `packages/app/src/lib/api.ts` — Conversation 接口
- `packages/app/src/pages/Chat.tsx` — loadConvDetail 恢复逻辑 + agent/provider Select 顺序
- `packages/app/src/components/Nav.tsx` — 导航栏分组结构
- `packages/app/src/routes.tsx` — NAV_ITEMS 排序

### CLI
- `chariot/cli/commands/chat.py` — _run 恢复逻辑
- `chariot/cli/repl.py` — ChatRepl 每轮刷新
- `chariot/cli/context.py` — ChatContext 组装 ChatRequest

### 测试
- `tests/agent/test_agent_profile_binding.py` — agent profile binding 全场景(工具挂载行为)

## 历史变更

- 2025-05-14: 补充配置优先级章节(Provider/Model/连接/Agent 四维度);last_model → last_provider 改名说明;UI 导航重构记录;验收标准增加 agent 覆盖 provider 场景;补充 Migration、时序图、边界情况、回滚策略四章。**工具调用行为变更**:无 toolset 不挂载工具;已补充到已完成部分、验收标准、测试覆盖、相关文件。
- 2025-05-13: 创建本文档,基于 0.8.8 OpenAI Provider 已完成工作。

## DB Migration 步骤

### 方案选择

**方案 A(推荐): Rename 列(破坏性小,但需改所有引用)**
- `ALTER TABLE conversations RENAME COLUMN last_model TO last_provider;`
- 优点:语义正确,无冗余列
- 缺点:需同步改所有读写该列的代码(Repo/CLI/Sidecar/前端)

**方案 B: 新增列 + 兼容读取**
- `ALTER TABLE conversations ADD COLUMN last_provider TEXT;`
- 启动时一次性 `UPDATE conversations SET last_provider = last_model WHERE last_provider IS NULL;`
- 优点:零停机迁移,旧代码可读旧列
- 缺点:多一列冗余,长期需清理

本阶段采用**方案 A**(因改动范围可控,且 `last_model` 语义错误应尽早纠正)。

### 具体 SQL

```sql
-- Step 1: 升级 schema
PRAGMA user_version = <当前版本+1>;

-- Step 2: rename 旧列
ALTER TABLE conversations RENAME COLUMN last_model TO last_provider;

-- Step 3: 新增列
ALTER TABLE conversations ADD COLUMN agent_profile TEXT;

-- Step 4: 重建索引(若 last_model 有索引)
-- DROP INDEX IF EXISTS idx_conversations_last_model;
-- CREATE INDEX idx_conversations_last_provider ON conversations(last_provider);
```

### 代码同步清单

- [ ] `chariot/database/models.py`: `ConversationRow.last_model` → `last_provider`
- [ ] `chariot/repos/conversation_repo.py`: 所有 `last_model` 引用
- [ ] `chariot/sidecar/services/conversation.py`: serialize 字段名
- [ ] `chariot/cli/commands/conversation.py` + `repl.py`: 展示字段名
- [ ] `packages/app/src/lib/api.ts`: `Conversation.last_model` → `last_provider`
- [ ] `packages/app/src/pages/Conversations.tsx`: 展示字段名(如有)

## 前后端交互时序图

### 场景 1: UI 新建对话并发送首条消息

```
User          Chat.tsx          Sidecar           AIAgent        DB
 |               |                 |                |             |
 |--选 agent:dev, provider:gpt4-->|                |             |
 |               |                 |                |             |
 |--输入 "hi" -->|                 |                |             |
 |               |--send_message-->|                |             |
 |               |  {conversation_id:null,          |             |
 |               |   provider_name:"gpt4",          |             |
 |               |   agent_profile:"dev", ...}     |             |
 |               |                 |--run_chat(req)->|            |
 |               |                 |                |--ensure_conversation()
 |               |                 |                |--write user msg
 |               |                 |                |--call Provider
 |               |                 |                |--write assistant msg
 |               |                 |                |--UPDATE conv
 |               |                 |                |  last_provider="gpt4"
 |               |                 |                |  agent_profile="dev"
 |               |                 |<--ChatEvent流--|             |
 |               |<----SSE/JSON-RPC流--------------|             |
 |<----渲染流----|                 |                |             |
```

### 场景 2: UI 切换已有对话

```
User          Chat.tsx          Sidecar           DB
 |               |                 |                |
 |--点对话列表-->|                 |                |
 |               |--get_conversation(id)->|        |
 |               |                 |--SELECT * FROM conversations
 |               |                 |  + SELECT messages
 |               |<--{id, last_provider, agent_profile, messages}|
 |               |                 |                |
 |--渲染对话历史 + 恢复 provider/agent 下拉--|
```

### 场景 3: CLI 接续对话

```
User    CLI(chat.py)    ChatContext    AIAgent      DB
 |          |                |            |          |
 |--chariot chat "hi" ------>|            |          |
 |   --conversation <id>     |            |          |
 |          |                |            |          |
 |          |--_restore_conversation_config(id)
 |          |                |            |          |
 |          |                |            |--SELECT last_provider, agent_profile
 |          |                |            |          |
 |          |                |            |--return ("gpt4", "dev")
 |          |                |            |          |
 |          |--provider = "gpt4"(恢复值)
 |          |--agent_profile = "dev"(恢复值)
 |          |                |            |          |
 |          |--若 CLI 传了 --provider anthropic
 |          |--provider = "anthropic"(CLI flag 覆盖)
 |          |                |            |          |
 |          |--构造 ChatRequest-->         |          |
 |          |                |--run_chat(req)        |
 |          |                |            |--...     |
```

## 边界情况与错误处理

### 1. 对话恢复值与 CLI flag 冲突

**场景:** `chariot chat "hi" --conversation <id> --provider anthropic`
- 对话上次用的 provider 是 `gpt4`
- **处理:** CLI flag 优先级更高,使用 `anthropic`
- **注意:** DB 中的 `last_provider` 仍保持 `gpt4`(不因本次覆盖而变)
- **原因:** 覆盖是"本次临时行为",不应污染对话持久化值

### 2. Agent binding 的 provider_profile 与 CLI --provider 冲突

**场景:** `--agent dev --provider gpt4`, 而 `dev.provider_profile = ollama`
- **处理:** agent_profile 优先级更高,实际路由到 `ollama`
- **注意:** CLI 的 `--base-url`/`--api-key` patch 是 keyed 到 `--provider` 的 entry name(`gpt4`)
- **风险:** patch 不会跟到 `ollama`,可能用错连接信息
- **缓解:** 日志里记录最终生效的 provider_name,方便排查

### 3. 对话不存在

**场景:** `chariot chat "hi" --conversation 12345`(非法 ULID 或已删除)
- **处理:** `_resolve_conversation_id()` 阶段 die(非法 ULID)
- **或:** AIAgent `ensure_conversation()` 发现不存在 → 新建(如果允许)
- **当前:** CLI 对非法 ULID 提前 die;UI 侧从列表选,不会选到不存在的

### 4. UI 和 CLI 同时修改同一对话

**场景:** 用户同时在 UI 和 CLI 操作同一个对话
- **处理:**
  - DB 写:双层锁(进程内 asyncio.Lock + SQLite BEGIN IMMEDIATE)保证串行
  - UI 读:每次 `loadConvDetail()` 重新查 DB,读到最新值
  - CLI REPL:每轮输入前重新查 conversation 最新配置(待实现)
- **竞态:** 若 UI 刚改了 agent,CLI REPL 下轮输入前重查,能拿到最新值

### 5. Agent profile 被删除

**场景:** 对话持久化了 `agent_profile="dev"`,但之后 `dev` 被删除
- **处理:**
  - UI: `loadConvDetail()` 返回 `"dev"`,但下拉框里找不到该选项 → 显示为空白或 `(deleted)`
  - CLI: `_resolve_binding()` 时 AgentService 找不到 profile → 报错或 fallback 到无绑定
- **建议:** 删除 agent profile 前检查是否被对话引用,或软删除(标记 disabled)

### 6. Provider entry 被删除

**场景:** 对话持久化了 `last_provider="gpt4"`,但 `gpt4` entry 被删除
- **处理:**
  - UI: `setSelectedEntry("gpt4")` 但 providers 列表里没有 → Select 显示 placeholder
  - CLI: `_resolve_provider_name()` 找不到 entry → die 提示
- **缓解:** Provider 删除前检查引用,或 UI 显示 "(missing: gpt4)"

## 回滚与兼容性策略

### 回滚方案

若本阶段实现后发现严重问题,回滚到上一版本:

```sql
-- 回滚 migration(需手动执行)
PRAGMA user_version = <上一版本>;
ALTER TABLE conversations DROP COLUMN agent_profile;
ALTER TABLE conversations RENAME COLUMN last_provider TO last_model;
```

**代码回滚:**
- git revert / reset 到上一 tag
- 或单独 revert DB migration commit + 功能 commit

### 向前兼容

**旧版本 chariot 读新版 DB:**
- 问题:旧代码读 `last_model` 列,但新版 DB 已 rename 为 `last_provider`
- **处理:** Migration 必须在所有节点同时升级(单用户单实例,无分布式问题)
- **缓解:** 若需兼容,采用方案 B(保留旧列,新增新列)

**新版本读旧版 DB:**
- 首次启动自动跑 migration
- `init_db()` 内嵌 migration runner,按 `PRAGMA user_version` 顺序执行

### 数据无损保证

- `agent_profile` 新增,不影响已有数据
- `last_model` → `last_provider` rename,数据原地保留
- 建议在 migration 前手动备份 DB 文件(文档里加提醒)

### 跨版本协作

- UI 和 CLI 共用同一份 DB,版本必须同步
- 若用户用旧 CLI + 新 UI,可能读写字段不一致
- **策略:** 在 sidecar 启动时检查 `user_version`,不匹配则拒绝启动并报错


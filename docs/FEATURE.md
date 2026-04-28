# Chariot 0.4.0 推进表

> **当前活跃**:`0.4.0`
> **上一版归档**:[`docs/history/0.3.1/FEATURE.md`](history/0.3.1/FEATURE.md)
>
> **0.4.0 主题**:Agent 进化第一步 —— 多轮对话记忆 + 工具调用。详细架构见
> [`DESIGN.md`](DESIGN.md)。

每步推进规则(沿用):每完成一步 → 跑验证 → 等用户确认"通过"再标 ✅,然后 commit。
一个 FEATURE 步骤 = 一个 commit。

---

## 0.4.0 patch 列表

### M.1 ✅ schema + 三表 ORM + Repos + migration v4

- migration v4(`chariot/server/database/migrations.py`):
  - `CREATE TABLE conversations (id, title, last_model, created_at, updated_at)` + `idx_conversations_updated`
  - `CREATE TABLE messages (id, conversation_id FK, seq, role, content, model_name, created_at)` + `idx_messages_conv` + `UNIQUE(conversation_id, seq)`
  - `CREATE TABLE tools (id, name UNIQUE, type, enabled, options, created_at, updated_at)` + `idx_tools_name`
- ORM(`chariot/server/database/models.py`):`ConversationRow` / `MessageRow` / `ToolRow`
- `ConversationRepo`(`chariot/server/repository/conversation_repo.py`):
  - `create(id, title=None) / get(id) / list(limit, offset) / delete(id) / update_title(id, title)`
  - `ensure_exists(id)` —— 不存在则 create(供 dataplane auto-create 用)
  - `append_message(conv_id, role, content, model_name=None)` —— seq 自增
  - `load_messages_as_anthropic(conv_id) -> list[dict]` —— 拼回 Anthropic 协议形态
- `ToolRepo`(`chariot/server/repository/tool_repo.py`):
  - `list_entries(s) / get(name) / update(name, enabled?, options?) / list_enabled(s)`
  - `seed_if_empty(s)` —— 写 4 条默认 disabled 行
  - **不暴露** create / delete(0.4.0 tools 表只允许改 enabled / options)
- 测试:`tests/server/test_conversation_repo.py` + `tests/server/test_tool_repo.py`
- **验证**:`pytest -q` + `ruff check` + `pyright`

### M.2 ✅ Tool ABC + ToolRegistry + 4 内置工具实现

- `chariot/server/tool/base.py`:`Tool` ABC(`name` / `from_config` / `schema` / `execute`)
- `chariot/server/tool/registry.py`:`ToolRegistry`(类比 ModelRegistry)
- 4 个实现:
  - `chariot/server/tool/readfile.py`:`ReadFileTool`(`max_bytes` 截断 + UTF-8 fallback base64)
  - `chariot/server/tool/listdir.py`:`ListDirTool`(`recursive` 选项)
  - `chariot/server/tool/shellexec.py`:`ShellExecTool`(workdir 下跑 + timeout;**直接 `asyncio.create_subprocess_exec`,不过 shell** —— 跨平台一致 + 免 quoting / shell metachar 注入,见 DESIGN §8.4)
  - `chariot/server/tool/httpget.py`:`HttpGetTool`(域白名单 + max_bytes)
- `chariot/server/tool/__init__.py`:四个 `ToolRegistry.register(...)` 显式调用
- `ToolEntry` / `ToolConfig`(`chariot/server/config.py` 加,与 `ModelEntry` / `ChariotConfig` 同结构)
- 测试:`tests/server/test_tools.py`(每个工具的 happy path + 边界:超大文件截断 / 不存在路径 / timeout / 白名单拦截)
- **验证**:`pytest -q` + `ruff check` + `pyright`

### M.3 ✅ Agent 接 ConversationRepo + 工具循环 + lifespan 接入

- `Agent` 类(`chariot/server/agent.py`)新字段:`_tools: dict[str, Tool]`
- `Agent.install_from_config(model_config, tool_config)` 替代 0.3.1 的单参版本
- `Agent.handle(body, *, conversation_id, stream, session)` 主流程改造:
  - 接 ConversationRepo(每次 handle 现 new,不挂在 Agent 上)
  - 有 conversation_id → ensure_exists + load history + prepend
  - 工具循环:while 检测 tool_use → 执行 → append tool_result → 重调 Model
  - max_iter 配置(env `CHARIOT_MAX_TOOL_ITER`,默认 10),超限 400 `tool_iter_exceeded`
  - 收敛后若 client 要 stream,重发一次 `model.respond(stream=True)`
- body.tools 注入:Agent 在调 Model 前,把所有 enabled tools 的 schema 收集塞进
  body.tools(若客户端已经传了,merge / 客户端优先?**0.4.0 决定:客户端优先,
  server 不覆盖客户端传的 body.tools**;若客户端没传,server 注入 enabled tools 的 schema)
- lifespan 改:加 `ToolRepo.seed_if_empty()` + `ToolConfig.from_db(s)` + 双 config 注入 install
- 测试更新:
  - `tests/server/test_agent.py` 扩:install_from_config 双参 / handle 工具循环路径
  - `tests/server/test_agent_tool_loop.py`(新):mock model 返 tool_use → 收敛 / max_iter 超限 / 工具失败传播
  - `tests/server/test_app_lifespan.py` 扩:tools seed
- **验证**:`pytest -q` + `ruff check` + `pyright`

### M.4 ✅ dataplane controller 接 X-Chariot-Conversation header

- `chariot/server/controller/dataplane.py`:
  - 读 `request.headers.get("X-Chariot-Conversation")`,ULID 正则校验
  - 校验失败 → 400 `invalid_conversation_id`
  - 透传到 `agent.handle(body, conversation_id=..., stream=..., session=...)`
- `tests/server/test_dataplane.py` 扩:
  - 不带 header → 行为同 0.3.1(stateless)
  - 带 header 且 conv 不存在 → auto-create + 写 messages 表
  - 带 header 且 conv 存在 → load history + 拼接
  - 非法 ULID → 400
- **验证**:`pytest -q`

### M.5 ✅ admin/conversations + admin/tools controller + Pydantic schema

- `chariot/server/controller/conversations.py`(新):
  - `GET /admin/conversations` / `GET /admin/conversations/{id}` / `DELETE` / `PATCH` / `POST`(显式创建)
  - schema:`ConversationOut` / `ConversationDetail`(含 messages 数组)/ `ConversationsListResponse` / `UpdateTitleRequest` / `CreateConversationRequest`
- `chariot/server/controller/tools.py`(新):
  - `GET /admin/tools`:列 4 条 + 各自 schema
  - `PUT /admin/tools/{name}`:改 enabled / options;校验 options JSON、type 不允许改
  - schema:`ToolOut` / `ToolsListResponse` / `UpdateToolRequest`
- `controller/runtime.py`:`StatusResponse` 加 `tools_enabled` / `conversations_count`
- 端点注册到 `chariot/server/app.py`
- 测试:`tests/server/test_admin_conversations.py` + `tests/server/test_admin_tools.py`
- **验证**:`pytest -q`

### M.6 ✅ SDK + CLI 同步

- SDK(`chariot/sdk/proxy_client.py`):加 7 方法
  - `list_conversations / get_conversation / delete_conversation / update_conversation / create_conversation`
  - `list_tools / update_tool`
- CLI:
  - `chariot/cli/commands/conversation.py`(新):`list / show / rm / rename`
  - `chariot/cli/commands/tool.py`(新):`list / enable / disable / config`
  - `chariot chat` 加 `--conversation <id>` 参数(可选;默认每次新建 ULID)
- 测试:`tests/sdk/test_client_admin.py` 扩 + `tests/cli/test_commands_conversation.py` + `tests/cli/test_commands_tool.py`
- **验证**:`pytest -q`

### M.7 ✅ 前端 Chat 页会话侧栏 + Tools 页

- `packages/app/src/lib/api.ts`:
  - 加 `Conversation` / `ConversationDetail` / `Message` / `AnthropicBlock` / `Tool` 类型
  - 加 `listConversations / getConversation / deleteConversation / updateConversationTitle / createConversation`
  - 加 `listTools / updateTool`
  - `StatusResponse` 加 `tools_enabled` / `conversations_count`
- `packages/app/src/lib/chat.ts`:加 `conversationId` 选项,经 `X-Chariot-Conversation` header 透传
- `packages/app/src/pages/Chat.tsx`:
  - 左侧 conversations 侧栏(列表 + `+ New` + rename / delete)
  - 右侧按 Anthropic content blocks 分支渲染:text 普通气泡 / tool_use 蓝色卡片(name + input JSON)/ tool_result 绿/红卡片(content 文本或嵌套 blocks)
  - 流式期间 pending overlay,完成后 `getConversation` 拉 canonical 替换(含 tool_use/tool_result blocks)
  - 首发自动创建 conversation(避免空 conv 堆积);`chariot.chat.active_conversation` + `chariot.chat.selected_entry` localStorage 持久化
- `packages/app/src/pages/Tools.tsx`(新):类比 Models 页,4 行 fold/expand;Button 充当 on/off toggle(没装 Switch 组件),KV 编辑器整体替换 options,只读展示 anthropic schema
- `packages/app/src/pages/Dashboard.tsx`:展示 `tools_enabled` / `conversations_count` + Tools/Chat 入口链接
- `packages/app/src/routes.tsx`:加 `/tools` 路由 + Tools nav
- **附带 server 修复**:`AnthropicModel._rewrite_for_upstream` 把 `stream` 参数同步写到 `body.stream`;否则 slow path 内部 `stream=False` 调用时 client 原 body 里 `stream=true` 没推平,上游返 SSE → JSON 解析炸 → `upstream_invalid_response` 502
- **验证**:`bun run typecheck` + `bun run build` + 手测发起带工具的多轮对话

### M.8 ✅ docs + 版本号 0.3.1 → 0.4.0

- `DESIGN.md` 已在归档时落盘,实施过程若有偏差再更新
- `FEATURE.md` 标 ✅ 进度
- `ROADMAP.md` v2 段调整:多轮记忆 / 工具调用 标 0.4.0 完成,仅保留进化循环 + 多 Agent 在 v2
- `README.md` 加 conversations / tools 用法段落,Chat 页 + CLI 用例
- `pyproject.toml` + `chariot/__init__.py` 升 `0.3.1 → 0.4.0`
- 全套验证(ruff / format / pyright / pytest / bun build)
- commit + 等用户 push

---

## 0.5.0+ 路标

详见 `docs/ROADMAP.md`。要点:

- **0.5.0**:Agent 自我进化循环(读 logs feedback 调权重 / 切 model / 修 prompt)
- **0.6.0**:多 Agent 实例(logs / conversations / tools 加 agent_id 维度)
- **0.7.0?**:工具调用流式优化(中间 turn 增量 stream;免"最后一轮重发")

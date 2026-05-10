# Development

> 本文只描述当前开发计划。长期路线见 `docs/LONGTERMPLAN.md`，历史归档见 `docs/history/<version>/DEVELOPMENT.md`。  
> 归档时必须原样复制当前 `DEVELOPMENT.md`，不能改写内容，也不能丢失信息。

## 当前阶段

当前推进 `Milestone A3: Memory platform`。

这一阶段要做的不是“再给聊天系统加一点历史摘要”，而是把 memory 从散落在 prompt、context、trace 里的隐式材料，收口成一套可写入、可检索、可注入、可维护、可收敛的长期记忆系统。

## 目标

把 memory 从“对话副产物”提升成“可管理的长期知识层”。

memory 系统要回答这些问题：
- 哪些内容应该被记住。
- 这些记忆属于什么类型。
- 记忆从哪里来，为什么被写入。
- 哪些记忆要在当前 turn 注入。
- 不同会话、工作区、provider 下哪些记忆应该优先。
- 记忆过多、重复或过时的时候怎么收敛。

## 工作原理

`Memory platform` 和 `Prompt system`、`Context management` 是不同层：

- `context` 管“这次 turn 有哪些材料可用”。
- `prompt` 管“把这些材料怎么组装成最终发给模型的内容”。
- `memory` 管“哪些信息值得跨 turn 持久保存，并在未来 turn 再次注入”。

也就是说：

- `context` 负责这次输入的临时选择。
- `prompt` 负责这次请求的文本装配。
- `memory` 负责跨轮、跨会话的长期可复用事实与偏好。

memory 不能再只是 `context` 里的一段候选输入，也不能把所有聊天历史都当 memory；它应该有自己的定义、自己的生命周期和自己的注入策略。

## Memory 结构

建议把 memory 看成四层：

- `memory entry`
  - 记忆类型、文本、元数据、来源、适用范围。
  - 这是“这条 memory 是什么”。
- `memory event`
  - 创建、更新、删除、归档、合并、提取、重写。
  - 这是“这条 memory 发生过什么”。
- `memory link`
  - 记忆和 conversation、workspace、provider、tag、trace 的关联。
  - 这是“这条 memory 适用于哪里”。
- `memory policy`
  - 检索、注入、优先级、上限、收敛规则。
  - 这是“这次 turn 为什么注入这条 memory”。

如果后续要继续演进，再加：
- `memory trace`
- `memory consolidation`
- `memory governance`

但本阶段先不做成复杂知识平台。

## Memory 类型

建议先明确四类显式 memory：

- `preference`
  - 用户长期偏好，例如语言、语气、格式、常用约束。
- `project_fact`
  - 项目事实，例如仓库约定、里程碑、架构边界。
- `instruction`
  - 稳定指令，例如工作风格、执行偏好、禁用规则。
- `lesson`
  - 从失败或成功中总结出的可复用经验。

后续可以继续扩展，但本阶段先围绕这四类把 CRUD 和注入打通。

## 运行流

一次 turn 里，memory 相关的链路应该是：

```text
User
  -> CLI / Desktop UI / Sidecar
  -> AIAgent
  -> MemoryRepo 读取可用 memory 与链接
  -> MemoryPolicy 判断当前 turn 需要哪些 memory
  -> ContextComposer / PromptComposer 叠加 memory slice
  -> Provider
  -> AIAgent
  -> Memory event / memory trace 记录
```

这里的边界是：

- `MemoryRepo` 管数据。
- `MemoryPolicy` 管检索与注入。
- `ContextComposer` / `PromptComposer` 只负责把 memory 片段放进当次 turn 的输入。
- `Memory capture` 只负责把可复用的事实抽取出来，不直接改 prompt。

## 管理流

memory 的管理入口分三层：

```text
User
  -> CLI / Desktop UI
  -> Sidecar RPC
  -> MemoryService
  -> MemoryRepo
  -> SQLite DB
```

按功能拆开看：

- 查看
  - `list_memories`
  - `show_memory`
  - `list_memory_events`
- 配置
  - `create_memory`
  - `update_memory`
  - `delete_memory`
  - `pin_memory`
  - `archive_memory`
- 收敛
  - `merge_memory`
  - `summarize_memory`
  - `archive_stale_memory`
- 检索
  - `search_memory`
- `list_memory_links`

## 自动捕获

本阶段允许做最小自动捕获，但只做“从现有行为里提炼可复用事实”，不做训练型总结，也不做重型知识图谱。

自动捕获的来源只限定在：

- `conversation`：会话里反复出现的稳定偏好或边界。
- `prompt trace`：某次 turn 已经被显式使用过的记忆线索。
- `provider / tool`：和具体模型、工具使用相关的稳定约束。
- `error / recovery`：失败后能指导后续行为的短句经验。

自动捕获的输出只允许是：

- 一条新的 `memory entry`
- 或对已有 `memory entry` 的补充 / 归档 / 去重

自动捕获的约束是：

- 默认只写入显式可复用事实，不写长篇摘要。
- 默认不覆盖用户手工录入的 memory。
- 默认不自动激活大范围记忆，只把候选项写进 memory 层，由 `MemoryPolicy` 决定是否注入。
- 如果无法明确归类，就先不写入。

本阶段先把自动捕获挂在：

- `conversation` 完成后
- `prompt trace` 落库后
- 以及明确的 tool / provider 异常后

先做“少量、稳定、可解释”的自动捕获，再考虑后续 consolidation。

## 检索与注入规则

### Retrieval policy

memory 检索要能按这些维度筛选：

- `workspace`
- `conversation`
- `provider`
- `tags`
- `recency`
- `pinned`

### Injection policy

memory 注入要能控制：

- `max_items`
- `max_chars`
- `priority_order`
- `pinned_first`

### Consolidation

memory 收敛要支持：

- merge duplicates
- archive stale items
- summarize similar items

## 本阶段交付

- `memories` 的统一数据模型。
- `memory_events` / `memory_links` 的最小物理基础。
- `create / show / delete / pin / archive` 这条记忆管理入口。
- memory 的检索和注入策略。
- 从 trace / conversation 提取 memory 的最小自动捕获。
- memory 收敛和去重的最小能力。
- 只读的 memory 查看界面。

## 本阶段不做

- `Milestone A4: Tool management`
- `Milestone A5: Provider management`
- `Milestone A6: Agent and task management`
- `Milestone A7: Artifact management`
- `Milestone B1 ~ B5`
- memory 插件市场
- 自动训练型知识图谱平台

## 执行顺序

1. 先定 `memory entry` / `memory event` / `memory link` / `memory policy` 的 schema 和 repo。
2. 再把当前已有的 `memories` 最小 CRUD 收口到统一 `MemoryRepo` 和 `MemoryService`。
3. 接上 explicit memory 的创建、查看、删除、pin / archive。
4. 接上 retrieval policy 和 injection policy。
5. 接上 conversation / prompt trace / tool / provider 的最小自动捕获。
6. 补 CLI、sidecar 和桌面端的只读查看面。
7. 最后补收敛、去重和 smoke demo。

## 验收标准

- 可以列出所有 memory 及其类型。
- 可以查看单条 memory 的来源、标签和适用范围。
- memory 的创建、删除、pin / archive 不会散落在多个模块里各自实现。
- memory 检索和注入是显式 policy，不依赖隐式代码路径。
- 自动捕获能解释一条 memory 为什么被写入。
- 自动捕获默认是小步、可解释、可回退，不会吞掉用户手工 memory。
- 收敛流程能解释重复 memory 为什么被合并。
- 现有 `prompt`、`context`、`tool`、`provider` 的链路不回退。

## 验收方法

先跑自动化测试，再做手工检查。

```powershell
uv run pytest tests/platform/test_foundations.py -q
uv run pytest tests/platform/test_memory_policy.py tests/platform/test_memory_capture.py -q
uv run pytest tests/agent/test_memory_auto_capture.py tests/agent/test_memory_injection.py -q
uv run pytest tests/cli/test_commands.py -q
uv run pytest tests/sidecar/test_context_methods.py -q
uv run pytest tests/sidecar/test_admin_methods.py -q
```

记忆模板 demo：

```powershell
uv run chariot memory add --kind preference --text "默认用中文输出，但保留关键 English terms" --tag demo
uv run chariot memory add --kind preference --text "回答尽量简洁，先给结论再给原因" --meta '{"scope":"global","source":"demo"}'
uv run chariot memory list
uv run chariot memory show <id>
uv run chariot memory delete <id>
```

如果要把记忆绑定到某个会话，可以再加一条：

```powershell
uv run chariot memory add --kind preference --text "这个会话里优先用中文" --conversation <conversation_id> --tag demo-cn
```

手工验收重点看：

- memory 列表能否反映类型和时间顺序。
- `show` 是否能看清单条 memory 的文本和 meta。
- `add` / `delete` 是否只影响 memory 层，不污染 prompt / context。
- 检索和注入是否能被后续 turn 真实消费。
- 如果 memory 被自动捕获，trace / 日志里能否看出来源。

## 当前约束

- 只做当前阶段需要的最小边界，不提前把自动知识图谱、训练型总结、远程记忆同步做进来。
- 新计划必须先得到确认，再覆盖本文件。
- 归档时只做 verbatim copy，不做内容重写。

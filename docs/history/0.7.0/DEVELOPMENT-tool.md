# Development

> 本文只描述当前开发计划。长期路线见 `docs/LONGTERMPLAN.md`，历史归档见 `docs/history/<version>/DEVELOPMENT.md`。  
> 归档时必须原样复制当前 `DEVELOPMENT.md`，不能改写内容，也不能丢失信息。

## 当前阶段

当前推进 `Milestone A4: Tool management`。

这一阶段要做的不是“把工具再包一层”，而是把工具从散落的配置、硬编码调用和临时约定里收口成一套可查看、可配置、可追踪、可被运行时消费的工具管理面。

## 目标

把工具管理从“代码里顺手定义一下”提升成独立系统。

工具系统要回答这些问题：
- 当前有哪些工具可用。
- 每个工具怎么配置。
- 哪些工具启用，哪些工具停用。
- 工具能力和约束是什么。
- 当前 turn 为什么会用到某个工具。
- 工具调用失败时发生了什么。

## 工作原理

`Tool management` 和 `Prompt system`、`Context management` 是不同层：

- `context` 管“这次 turn 有哪些材料可用”。
- `prompt` 管“把这些材料怎么组装成最终发给模型的内容”。
- `tool` 管“模型能调用哪些外部能力、这些能力怎么配置、怎么启用、怎么追踪”。

也就是说：

- `context` 负责输入材料的选择。
- `prompt` 负责文本装配。
- `tool` 负责可执行能力的注册和调度。

工具不能再散落在 provider、agent、sidecar、UI 各处各自解释；它应该有自己的定义、自己的状态和自己的查看面。

## Tool 结构

建议把工具看成三层：

- `tool definition`
  - 工具名、类型、描述、输入 schema、能力说明。
  - 这是“这个工具是什么”。
- `tool config`
  - 启用状态、选项、参数、默认值。
  - 这是“这个工具怎么工作”。
- `tool runtime state`
  - 最近一次 probe、最近一次调用结果、错误状态、可用性。
  - 这是“这个工具当前能不能用、用了会怎样”。

如果后续要继续演进，再加：
- `tool version`
- `tool trace`
- `tool policy`

但本阶段先不做成复杂插件平台。

## 运行流

一次 turn 里，工具相关的链路应该是：

```text
User
  -> CLI / Desktop UI / Sidecar
  -> AIAgent
  -> ToolRepo 读取工具定义与配置
  -> ToolPolicy 判断当前是否可用
  -> PromptComposer 把 tool instruction / tool choice 叠进 prompt
  -> Provider
  -> AIAgent
  -> Tool execution / tool trace 记录
```

这里的边界是：

- `ToolRepo` 管数据。
- `ToolPolicy` 管可用性和约束。
- `PromptComposer` 只负责把工具说明放进 prompt。
- `Tool execution` 只负责真正执行工具。

## 管理流

工具的管理入口分三层：

```text
User
  -> CLI / Desktop UI
  -> Sidecar RPC
  -> ToolService
  -> ToolRepo
  -> SQLite DB
```

按功能拆开看：

- 查看
  - `list_tools`
  - `show_tool`
- 配置
  - `enable_tool`
  - `disable_tool`
  - `config_tool`
- 验证
  - `probe_tool`

## 本阶段交付

- 工具定义和配置的统一数据模型。
- 工具启用 / 停用 / 配置入口。
- 工具能力和约束的明确表达。
- 工具状态和最近一次探测结果。
- 只读的工具查看界面。
- 工具调用和失败的最小 trace。

## 本阶段不做

- `Milestone A5: Provider management`
- `Milestone A6: Agent and task management`
- `Milestone A7: Artifact management`
- `Milestone B1 ~ B5`

## 执行顺序

1. 先定 `tool definition` / `tool config` / `tool runtime state` 的 schema 和 repo。
2. 再把当前已有的工具注册逻辑收口到统一的 `ToolRepo`。
3. 接上启用、停用、配置、探测能力。
4. 补 CLI、sidecar 和桌面端的只读查看面。
5. 补工具调用 trace 和失败回放。
6. 最后补测试和一个可跑通的 smoke demo。

## 验收标准

- 可以列出所有工具及其状态。
- 可以查看单个工具的配置、能力和最近状态。
- 启用 / 停用 / 配置不会散落在多个模块里各自实现。
- 工具可用性判断是显式的，不依赖隐式代码路径。
- 工具 trace 能解释一次调用为什么发生、结果是什么。
- 现有 `prompt` 和 `context` 的链路不回退。

## 验收方法

先跑自动化测试，再做手工检查。

```powershell
uv run pytest tests/platform/test_tool_foundations.py -q
uv run pytest tests/sidecar/test_tool_methods.py -q
uv run pytest tests/cli/test_tool_commands.py -q
```

手工演示建议：

```powershell
uv run chariot tool list
uv run chariot tool show http_get
uv run chariot tool config http_get -o "allowed_domains=[`"example.com`"]"
uv run chariot tool enable http_get
uv run chariot tool probe http_get
```

手工验收重点看：

- 工具列表能否反映启用状态和配置。
- `show` 是否能看清一个工具的能力和选项。
- `config` 是否只影响工具配置，不污染别的层。
- `probe` 是否能给出明确可用性结果。
- 如果工具失败，trace / 日志里能否看出原因。

## 当前约束

- 只做当前阶段需要的最小边界，不提前把插件市场、自动安装、远程同步做进来。
- 新计划必须先得到确认，再覆盖本文件。
- 归档时只做 verbatim copy，不做内容重写。

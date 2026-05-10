# Development

> 本文只描述当前开发计划。长期路线见 `docs/LONGTERMPLAN.md`，历史归档见 `docs/history/<version>/DEVELOPMENT.md`。  
> 归档时必须原样复制当前 `DEVELOPMENT.md`，不能改写内容，也不能丢失信息。

## 当前阶段

当前推进 `Milestone A5: Provider management`。

这一阶段要做的不是“再给 provider 加几条 CRUD 命令”，而是把 provider 从普通配置项提升成一套可路由、可回退、可观测、可热更新的模型提供方平台。

## 目标

把 provider 管理从“表里的一条 entry”提升成“请求调度入口”。

provider 系统要回答这些问题：
- 当前有哪些 provider 可用。
- 每个 provider 的 profile 和 capabilities 是什么。
- 当前 turn 为什么会走这个 provider。
- 哪些 provider 是默认入口，哪些场景会覆盖。
- provider 失败时是否回退，回退到哪里。
- provider 配置修改后能不能热生效。
- provider 调用的健康、延迟和成本如何追踪。

## 工作原理

`Provider management` 和 `Prompt system`、`Context management`、`Tool management` 是不同层：

- `context` 管“这次 turn 有哪些材料可用”。
- `prompt` 管“把这些材料怎么组装成最终发给模型的内容”。
- `tool` 管“模型能调用哪些外部能力、这些能力怎么配置、怎么启用、怎么追踪”。
- `provider` 管“这次 turn 最终交给哪一个模型后端、为什么交给它、失败时怎么办”。

也就是说：

- `context` 负责输入材料的选择。
- `prompt` 负责文本装配。
- `tool` 负责可执行能力的注册和调度。
- `provider` 负责模型后端的选择、路由、健康和回退。

provider 不能再只被当成 `model` 的同义词，也不能散落在 agent、sidecar、UI 各处各自解释；它应该有自己的定义、自己的状态和自己的查看面。

## Provider 结构

建议把 provider 看成三层：

- `provider profile`
  - provider 名称、类型、模型、基础地址、凭据来源、能力标记、重试策略、超时策略。
  - 这是“这个 provider 是什么、能做什么”。
- `provider routing`
  - 默认 provider、按会话覆盖、按工作区覆盖、按角色覆盖、按任务覆盖、fallback 顺序。
  - 这是“这次 turn 为什么走它”。
- `provider runtime state`
  - health 状态、最近一次 probe、最近一次成功、最近一次失败、延迟、成本摘要。
  - 这是“这个 provider 当前能不能用、用起来怎么样”。

如果后续要继续演进，再加：
- `provider trace`
- `provider cost events`
- `provider policy`

但本阶段先不做成复杂调度平台。

## 运行流

一次 turn 里，provider 相关的链路应该是：

```text
User
  -> CLI / Desktop UI / Sidecar
  -> AIAgent
  -> ProviderRepo 读取 provider profile 与配置
  -> ProviderPolicy 判断当前路由、fallback、健康状态
  -> Request normalization 结合 provider capabilities 收口请求
  -> Provider
  -> AIAgent
  -> Provider trace / health update 记录
```

这里的边界是：

- `ProviderRepo` 管数据。
- `ProviderPolicy` 管路由和回退。
- `ProviderProber` 管可用性验证。
- `Request normalization` 只负责按 capabilities 裁剪请求字段。
- `Provider` 只负责真正执行模型调用。

## 管理流

provider 的管理入口分三层：

```text
User
  -> CLI / Desktop UI
  -> Sidecar RPC
  -> ProviderService
  -> ProviderRepo
  -> SQLite DB
```

按功能拆开看：

- 查看
  - `list_providers`
  - `show_provider`
  - `get_provider_status`
- 配置
  - `add_provider`
  - `update_provider`
  - `delete_provider`
  - `set_default_provider`
- 验证
  - `probe_provider`
- 路由
  - 默认 provider
  - 会话级覆盖
  - fallback 选择

## 本阶段交付

- provider profile 的统一数据模型。
- provider capabilities 和默认路由的明确表达。
- provider health / probe / status 的统一入口。
- provider fallback 和热切换的最小边界。
- 只读的 provider 查看界面。
- provider 调用、失败和健康变化的最小 trace。
- provider 配置修改后的热生效路径。

## 本阶段不做

- `Milestone A6: Agent and task management`
- `Milestone A7: Artifact management`
- `Milestone B1 ~ B5`
- provider 插件市场
- 远程 provider 同步
- 企业级 billing / quota 平台

## 执行顺序

1. 先定 `provider profile` / `provider routing` / `provider runtime state` 的 schema 和 repo。
2. 再把现有 provider entry CRUD 收口到统一的 `ProviderRepo` 和 `ProviderService`。
3. 接上默认 provider、会话覆盖和 fallback 选择。
4. 接上 probe、status 和 health 追踪。
5. 补 CLI、sidecar 和桌面端的只读查看面。
6. 补 provider 调用 trace 和失败回放。
7. 最后补测试和一个可跑通的 smoke demo。

## 验收标准

- 可以列出所有 provider 及其默认状态。
- 可以查看单个 provider 的 profile、capabilities 和最近健康状态。
- 默认切换、配置修改和 probe 不会散落在多个模块里各自实现。
- provider 可用性判断是显式的，不依赖隐式代码路径。
- fallback 规则能解释一次路由为什么发生、失败后去了哪里。
- provider trace 能解释一次调用为什么发生、结果是什么。
- 现有 `prompt`、`context` 和 `tool` 的链路不回退。

## 验收方法

先跑自动化测试，再做手工检查。

```powershell
uv run pytest tests/providers/test_registry.py -q
uv run pytest tests/providers/test_prober.py -q
uv run pytest tests/sidecar/test_admin_methods.py -q
uv run pytest tests/cli/test_commands.py -q
```

手工演示建议：

```powershell
uv run chariot provider list
uv run chariot provider show
uv run chariot provider use mock
uv run chariot provider probe mock
uv run chariot provider status
```

手工验收重点看：

- provider 列表能否反映默认状态和类型。
- `show` 是否能看清一个 provider 的 profile 和选项。
- `use` 是否只影响默认路由，不污染别的层。
- `probe` 是否能给出明确可用性结果。
- `status` 是否能说明健康、延迟和最近错误。
- 如果 provider 失败，trace / 日志里能否看出原因。

## 当前约束

- 只做当前阶段需要的最小边界，不提前把 provider 插件市场、自动路由策略引擎、远程同步做进来。
- 新计划必须先得到确认，再覆盖本文件。
- 归档时只做 verbatim copy，不做内容重写。

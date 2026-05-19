# C1: runtime / service boundary refactor plan

> 状态: 规划中 | 分支: `feat/0.8.9-boundary`

## 背景

当前仓库的边界并不一致:

- surface 层已经开始通过 `chariot/services/*` 访问领域能力
- `chariot/agent/run.py` 仍直接构造并调用多个 repo
- `chariot/agent/loop.py` 直接依赖 `ConversationRepo`
- `AIAgent.session_maker` 仍向外暴露底层 session,允许 surface 继续直穿 repo

这导致 `services` 层的定位不稳定:

- 一部分场景里它是统一入口
- 另一部分场景里它只是 repo proxy
- runtime 核心编排仍然和持久化细节强耦合

本次调整的目标不是一次性“消灭全部 repo”,而是先把 runtime 核心和 repo 的直接耦合拆开,建立稳定边界,再逐步把业务编排下沉到 service 层。

## 现状问题

### 1. runtime 直接感知 repo 细节

`chariot/agent/run.py` 当前直接使用:

- `ConversationRepo`
- `ContextRepo`
- `PromptRepo`
- `MemoryRepo`
- `ProviderRepo`
- `ToolRepo`
- `AuxiliaryRepo`

其中有些调用属于 bootstrap 装载,有些调用属于单轮对话编排。两类责任现在混在同一个入口里。

### 2. `AgentLoop` 的持久化接口过低级

`AgentLoop` 当前接收 `ConversationRepo | None`,并直接调用:

- `append_message()`

这使 loop 无法脱离 repo 存在,也让后续引入更高层的 conversation persistence service 变得困难。

### 3. `services` 层大多还是薄代理

当前多个 service 文件的注释已经明确说明:

- 现在只是 thin wrapper around repo
- 未来再把业务规则抽到这一层

这意味着现在还不能简单把 runtime 中的 repo 调用“机械替换”为 service,否则只是把 repo proxy 套一层名字。

### 4. surface 仍可绕过 services

`AIAgent.session_maker` 继续暴露 session 给 CLI / sidecar 直接调 repo。只要这个出口存在,边界约束就不成立。

## 重构目标

### 目标 1: 建立 runtime 的最小持久化接口

先把 `AgentLoop` 从 `ConversationRepo` 解耦,改为依赖一个最小协议或 service:

- 持久化 assistant message
- 持久化 tool result message

loop 不再知道 repo 类型,只知道“如何落库当前 turn 结果”。

### 目标 2: 把单轮对话编排聚合到 service 层

将 `run.py` 中与 stateful turn 强相关的持久化步骤收敛到可组合的 service 中:

- ensure conversation
- persist new user messages
- load history
- record context snapshot / trace
- record prompt trace
- capture memory

`run.py` 保留 orchestration,但不再直接操作 repo 细节。

### 目标 3: 区分 bootstrap 和 runtime

bootstrap 负责:

- 初始化 DB
- seed / sync 配置型数据
- 读取 provider / tool / auxiliary / capability 配置

runtime 负责:

- 对话执行
- 上下文组装
- prompt trace / memory capture
- conversation message persistence

两类逻辑应拆成清晰的边界,避免一个类同时承担“系统装载器”和“对话执行器”。

### 目标 4: 收紧 surface 对 repo 的访问路径

逐步让 surface 只依赖:

- `chariot/services/*`
- `AIAgent` 暴露的稳定领域接口

最终移除 `session_maker` 这种 repo 逃逸口。

## 非目标

本轮不做以下事项:

- 不调整 DB schema
- 不重写全部 `services/*`
- 不把所有 repo 一次性迁走
- 不改变 chat 行为、tool 行为、memory 策略本身
- 不处理 sidecar 全量 API 重命名

## 分阶段计划

### Phase 1: 为 loop 引入最小持久化边界

输出:

- 新增一个 conversation persistence 协议或 service
- `AgentLoop` 改为依赖该协议,不再依赖 `ConversationRepo`

动作:

- 提炼 `append assistant message`
- 提炼 `append tool result message`
- 保持 stateful / stateless 行为不变

验收:

- `chariot/agent/loop.py` 不再 import `ConversationRepo`
- 现有 loop / tool execution / trace 相关测试通过

### Phase 2: 收敛 `run.py` 的 stateful turn 持久化

输出:

- conversation turn service
- context trace service
- prompt trace / memory capture 经 service 暴露

动作:

- 提炼 `_persist_new_user_messages`
- 提炼 `_load_history_as_messages`
- 提炼 context snapshot / trace 记录逻辑
- 提炼 memory capture 逻辑

验收:

- `run.py` 中 stateful turn 路径不再直接 new `ConversationRepo` / `ContextRepo`
- 事务边界保持不变
- conversation state persistence 测试通过

### Phase 3: 收敛 runtime 读取类 repo 调用

输出:

- prompt / memory / agent profile 的稳定 service 接口

动作:

- 将 prompt bundle 读取与 trace 记录统一收敛
- 将 memory retrieval 与 capture 统一收敛
- 将 agent profile / toolset 解析统一收敛

验收:

- `run.py` 中 repo import 数量明显下降
- service 命名与职责清晰

### Phase 4: 收紧 surface 边界

输出:

- surface 改走 service
- `session_maker` 暴露范围缩小或删除

动作:

- 盘点 CLI / sidecar 中仍直接依赖 repo 的位置
- 优先替换常用读写路径
- 保留必要兼容层,但标注弃用

验收:

- 主要 surface 不再需要 `async with agent.session_maker()`
- repo 访问入口集中

## 建议落地顺序

为了避免一次改太大,本次分支建议按下面顺序推进:

1. 先改 `AgentLoop` 依赖边界
2. 再改 `run.py` 的 stateful turn 路径
3. 再改 prompt / memory / agent binding 的读取与写入服务
4. 最后再处理 `session_maker` 暴露和 surface 收口

这个顺序的原因是:

- `AgentLoop` 边界最清晰,回归风险最低
- `run.py` 是核心编排入口,应在 loop 边界稳定后再动
- surface 收口依赖前两步,否则只是表面替换

## 代码组织建议

建议优先补齐以下 service 能力,而不是继续在 `run.py` 中堆 helper:

- `ConversationService`
  - `ensure_exists`
  - `append_user_messages`
  - `append_assistant_message`
  - `append_tool_results`
  - `load_history_as_messages`

- `ContextService`
  - `record_snapshot`
  - `record_trace`

- `PromptService`
  - `resolve_bundle_for_request`
  - `record_prompt_trace`

- `MemoryService`
  - `list_relevant_entries`
  - `capture_turn`
  - `capture_error`

- `AgentService`
  - `resolve_agent_binding`

## 风险

### 1. 事务边界被不小心拆散

如果把 repo 替成自动开 session 的 proxy service,可能会把本来同一轮 turn 内的操作拆成多个 session,破坏一致性。

要求:

- stateful turn 内的关键路径必须支持显式复用同一个 session

### 2. service 继续停留在“换皮代理”

如果只是把 `repo.xxx()` 改成 `service.xxx()` 但不承载业务语义,代码会更绕,不会更清晰。

要求:

- 新 service 方法必须体现业务动作,而不是单纯转发 repo 方法名

### 3. 测试覆盖不足

这次改动触及:

- 对话持久化
- tool loop
- context trace
- memory capture

要求:

- 先保住现有测试
- 对新抽象新增最少必要单测

## 验收标准

- `AgentLoop` 不再依赖具体 repo 类型
- `run.py` 的 stateful turn 不再直接构造 conversation/context repo
- service 层开始承载真实业务语义,不只是 session proxy
- 不改变现有 chat / tool / memory 的外部行为
- 现有相关测试保持通过

## 当前落地状态

已完成:

- `AgentLoop` 已从 `ConversationRepo` 切到最小 message store 边界
- `run.py` 的 stateful turn 已改走 `ConversationService` / `ContextService`
- `run.py` 的 prompt / memory 主链已改走 `PromptService` / `MemoryService`
- `run.py` 的 agent binding 解析已改走 `AgentService` / `ToolsetService`
- `bootstrap()` 的 provider / tool / prompt / auxiliary 装载已改走 services

暂不移除:

- `bootstrap()` 阶段的 repo 直连
- `AIAgent.session_maker` 兼容出口

原因:

- `bootstrap()` 仍承担系统装载职责,后续若继续拆,应单独抽成 runtime/bootstrap service
- `session_maker` 目前仍被 CLI / sidecar / eval / tests 广泛使用,直接删除会造成较大外部改动面

## 本分支首个落点

本分支第一步只做:

- 为 `AgentLoop` 提炼最小 conversation persistence 接口
- 保持 `run.py` 其他逻辑先不动

先把最深的一层耦合切开,再继续往上收敛。

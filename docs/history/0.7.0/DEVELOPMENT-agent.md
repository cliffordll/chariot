# Development

## A6 Test And Demo

Current A6 detailed test, smoke-demo, and remaining-work notes are documented in:

- `docs/guides/task-management-demo.md`

That guide includes:

- automated regression commands
- CLI smoke steps for `agent profile`, `task`, `task run`, `delegation`, and `scheduled job`
- current sidecar RPC surface for A6
- unfinished items after the minimum A6 implementation
- recommended next backend steps

> 本文只描述当前开发计划。长期路线见 `docs/LONGTERMPLAN.md`，历史归档见 `docs/history/<version>/DEVELOPMENT.md`。  
> 归档时必须原样复制当前 `DEVELOPMENT.md`，不能改写内容，也不能丢失信息。

## 当前阶段

当前推进 `Milestone A6: Agent and task management`。

这一阶段要做的不是“给现有 chat 再包一层任务壳”，而是把 agent、delegation、后台执行从零散的运行时行为，收口成一套可定义、可调度、可查询、可恢复的正式对象模型。

## 目标

把 agent / task 从“临时一次性执行过程”提升成“可管理的长期运行单元”。

这一阶段要回答这些问题：
- 当前系统里有哪些 agent profile。
- 一次任务的目标、状态、父子关系和产物是什么。
- 哪些任务正在运行，哪些任务已经暂停、失败或完成。
- 后台执行和前台即时执行的边界是什么。
- delegation 创建的子任务怎样被记录、追踪和恢复。
- 定时任务怎样运行，为什么运行，运行后留下什么结果。

## 工作原理

`Agent and task management` 和 `Prompt system`、`Context management`、`Memory platform`、`Tool management`、`Provider management` 是不同层：

- `prompt` 管“发给模型的内容怎么组织”。
- `context` 管“当前 turn 有哪些输入材料”。
- `memory` 管“哪些信息跨 turn 持久复用”。
- `tool` 管“模型可以调用哪些外部能力”。
- `provider` 管“请求发给哪个模型、以什么能力运行”。
- `agent/task` 管“谁在执行目标、执行到哪里、和谁协作、结果落在哪里”。

也就是说：

- `AIAgent` 仍然负责单次请求和 tool loop 编排。
- `agent profile` 负责定义“某类 agent 应该怎么工作”。
- `task` 负责定义“这次工作是什么、当前状态是什么、和别的任务有什么关系”。
- `task run` 负责记录“一次具体执行尝试发生了什么”。
- `scheduled job` 负责定义“哪些后台工作需要周期性触发”。

本阶段不做复杂的分布式自治系统，但要先把 agent / task / job 的对象边界定清楚。

## Agent 结构

建议把 agent 管理拆成三层：

- `agent profile`
  - role、prompt bundle、tool profile、provider profile、budget。
  - 这是“这个 agent 以什么身份和约束运行”。
- `task`
  - task id、goal、status、parent / child、owner、artifacts。
  - 这是“这件事是什么，现在做到哪里了”。
- `task run`
  - run id、started_at、finished_at、result、error、resume_from。
  - 这是“某次执行尝试发生了什么”。

如果后续继续演进，再加：
- `delegation policy`
- `agent memory scope`
- `task approval`
- `run checkpoint`

但本阶段先不做复杂自治编排平台。

## Task 类型

建议先明确几类最小任务类型：

- `interactive`
  - 前台即时任务，通常由 CLI / UI 用户直接触发。
- `background`
  - 后台长任务，允许脱离当前前端会话继续运行。
- `delegated`
  - 由父任务派生出来的子任务，用于并行或分工。
- `scheduled`
  - 由 job 周期性触发的任务，例如清理、评估、整理。

后续可以继续扩展，但本阶段先围绕这四类把模型、状态和查询入口打通。

## 运行流

一次任务执行里，agent / task 相关链路应该是：

```text
User / Scheduler
  -> CLI / Desktop UI / Sidecar
  -> AgentService / TaskService
  -> AgentProfileRepo / TaskRepo / JobRepo
  -> Runtime creates task run
  -> AIAgent executes work
  -> Delegation creates child task if needed
  -> Artifacts / logs / traces persist
  -> Task run closes
  -> Task status updates
```

这里的边界是：

- `AgentProfileRepo` 管 agent profile 数据。
- `TaskRepo` 管 task / task run 状态。
- `JobRepo` 管定时任务配置与最近运行记录。
- `AIAgent` 只负责执行，不负责保存复杂任务状态模型。
- `delegation` 负责创建和关联子任务，不直接承担持久化职责。

## 管理流

agent / task 的管理入口分三层：

```text
User
  -> CLI / Desktop UI
  -> Sidecar RPC
  -> AgentService / TaskService / JobService
  -> Repo layer
  -> SQLite DB
```

按功能拆开看：

- Agent
  - `list_agents`
  - `show_agent`
- Task
  - `list_tasks`
  - `show_task`
  - `cancel_task`
  - `pause_task`
  - `resume_task`
- Job
  - `list_jobs`
  - `show_job`
  - `run_job_now`

## Background execution

本阶段允许做最小后台执行，但只做“可见、可查、可取消”的基础设施，不做复杂分布式队列。

后台执行至少要支持：

- 创建后台 task 后，状态从 `queued` 进入 `running`。
- 任务完成后进入 `completed`，失败后进入 `failed`，取消后进入 `cancelled`。
- 任务暂停后可进入 `paused`，恢复后产生新的 `task run` 或继续原 run。
- 后台执行结果必须能被 CLI / sidecar / UI 查询。

后台执行的约束是：

- 默认先基于单机本地运行时，不提前引入远程 worker。
- 默认不做复杂优先级抢占，只保留状态与顺序语义。
- 默认不把所有中间过程都提升成 task，只有有明确 owner 和状态需求的执行才建 task。
- 如果任务不能安全恢复，就必须显式标记为不可恢复。

## Delegation

本阶段对 delegation 的要求是：

- 父任务可以创建子任务。
- 子任务必须记录 `parent_task_id`。
- 父任务可以查询子任务状态汇总。
- 子任务产物和日志要能回链到父任务。
- 如果 delegation 失败，失败信息要落在 task / run 层，而不是只留在临时输出里。

先把 delegation 作为 task graph 的最小能力做起来，再考虑更复杂的多 agent 协商。

## Scheduled jobs

scheduled job 至少要支持：

- job definition
  - 名称、目标、周期、启用状态。
- recent runs
  - 最近一次开始时间、结束时间、状态、错误摘要。
- manual trigger
  - 允许手工立刻运行一次。

建议第一批 job 只覆盖：

- periodic cleanup
- periodic curation
- periodic eval

## 状态模型

建议最小 task 状态机为：

- `queued`
- `running`
- `paused`
- `completed`
- `failed`
- `cancelled`

建议最小 job 状态字段为：

- `enabled`
- `last_run_status`
- `last_run_at`
- `next_run_at`

## 本阶段交付

- `agent_profiles`、`tasks`、`task_runs`、`scheduled_jobs` 的最小数据模型。
- agent / task / job 的统一 repo 和 service 入口。
- `list / show` 级别的 agent 与 task 查询能力。
- task 的 `pause / resume / cancel` 最小控制面。
- delegation 的父子任务关系与最小追踪能力。
- 后台任务状态查看入口。
- job 的列表与手工触发入口。

## 本阶段不做

- `Milestone A7: Artifact management`
- `Milestone B1 ~ B5`
- 远程分布式 worker 集群
- 复杂抢占式调度器
- 多 agent 自主协商协议
- 完整 checkpoint 恢复平台

## 执行顺序

1. 先定 `agent profile` / `task` / `task run` / `scheduled job` 的 schema 和 repo。
2. 再补统一的 `AgentService`、`TaskService`、`JobService`。
3. 接上 task 的 `list / show / pause / resume / cancel` 基础能力。
4. 接上 delegation 的父子任务关系和最小追踪。
5. 接上 CLI、sidecar 和桌面端的只读查看面。
6. 最后补 manual trigger、后台执行 smoke demo 和测试。

## 验收标准

- 可以列出所有 agent profile、task、job。
- 可以查看单个 task 的目标、状态、父子关系和最近运行情况。
- task 状态流转不是散落在多个模块里的隐式逻辑。
- delegation 能解释子任务从哪里来、现在在哪个状态。
- 后台任务状态可被查询，不再只是一次性运行。
- job 的最近运行结果可以被查看。
- 现有 `prompt`、`context`、`memory`、`tool`、`provider` 链路不回退。

## 验收方法

先跑自动化测试，再做手工检查。

```powershell
uv run pytest tests/platform/test_agent_foundations.py -q
uv run pytest tests/platform/test_task_runtime.py -q
uv run pytest tests/sidecar/test_agent_methods.py tests/sidecar/test_task_methods.py -q
uv run pytest tests/cli/test_agent_commands.py tests/cli/test_task_commands.py -q
```

手工演示建议：

```powershell
uv run chariot agent list
uv run chariot task list
uv run chariot task show <id>
uv run chariot job list
```

手工验收重点看：

- agent 列表能否反映 role、profile 和预算等基础属性。
- `task show` 是否能看清目标、状态、父子关系和最近 run。
- `pause / resume / cancel` 是否只影响 task 层，不污染 prompt / provider 等别的层。
- background task 和 scheduled job 的状态是否能在界面或 RPC 里看出来。
- 如果 delegation 失败，日志或任务详情里能否看出原因。

## 当前约束

- 只做当前阶段需要的最小边界，不提前把复杂自治系统、分布式调度、跨机器恢复做进来。
- 新计划必须先得到确认，再覆盖本文件。
- 归档时只做 verbatim copy，不做内容重写。
## A6 Test And Demo

Current A6 detailed test and smoke-demo instructions are documented in:

- `docs/guides/task-management-demo.md`

This guide covers:

- automated regression commands
- CLI smoke steps for `agent profile`, `task`, `task run`, `delegation`, and `scheduled job`
- current sidecar RPC surface for A6
- expected checkpoints for manual verification

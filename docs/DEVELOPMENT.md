# Development

> 本文只描述当前开发计划。长期路线见 `docs/LONGTERMPLAN.md`,历史归档见 `docs/history/<version>/DEVELOPMENT.md`。
> 归档时必须原样复制当前 `DEVELOPMENT.md`,不能改写内容,也不能丢失信息。

## 当前阶段

当前推进 `Milestone A6: Agent and task management`。

这一阶段要做的不是"给现有 chat 再包一层任务壳",而是把 agent、delegation、后台执行从零散的运行时行为,收口成一套可定义、可调度、可查询、可恢复的正式对象模型。

## 目标

把 agent / task 从"临时一次性执行过程"提升成"可管理的长期运行单元"。

这一阶段要回答这些问题:
- 当前系统里有哪些 agent profile。
- 一次任务的目标、状态、父子关系和产物是什么。
- 哪些任务正在运行,哪些任务已经暂停、失败或完成。
- 后台执行和前台即时执行的边界是什么。
- delegation 创建的子任务怎样被记录、追踪和恢复。
- 定时任务怎样运行,为什么运行,运行后留下什么结果。

## 工作原理

`Agent and task management` 和 `Prompt system`、`Context management`、`Memory platform`、`Tool management`、`Provider management` 是不同层:

- `prompt` 管"发给模型的内容怎么组织"。
- `context` 管"当前 turn 有哪些输入材料"。
- `memory` 管"哪些信息跨 turn 持久复用"。
- `tool` 管"模型可以调用哪些外部能力"。
- `provider` 管"请求发给哪个模型、以什么能力运行"。
- `agent/task` 管"谁在执行目标、执行到哪里、和谁协作、结果落在哪里"。

也就是说:

- `AIAgent` 仍然负责单次请求和 tool loop 编排。
- `agent profile` 负责定义"某类 agent 应该怎么工作"。
- `task` 负责定义"这次工作是什么、当前状态是什么、和别的任务有什么关系"。
- `task run` 负责记录"一次具体执行尝试发生了什么"。
- `scheduled job` 负责定义"哪些后台工作需要周期性触发"。

本阶段不做复杂的分布式自治系统,但要先把 agent / task / job 的对象边界定清楚。

## Agent 结构

建议把 agent 管理拆成三层:

- `agent profile`
  - role、prompt bundle、tool profile、provider profile、budget。
  - 这是"这个 agent 以什么身份和约束运行"。
- `task`
  - task id、goal、status、parent / child、owner、artifacts。
  - 这是"这件事是什么,现在做到哪里了"。
- `task run`
  - run id、started_at、finished_at、result、error、resume_from。
  - 这是"某次执行尝试发生了什么"。

如果后续继续演进,再加:
- `delegation policy`
- `agent memory scope`
- `task approval`
- `run checkpoint`

但本阶段先不做复杂自治编排平台。

## Task 类型

建议先明确几类最小任务类型:

- `interactive`
  - 前台即时任务,通常由 CLI / UI 用户直接触发。
- `background`
  - 后台长任务,允许脱离当前前端会话继续运行。
- `delegated`
  - 由父任务派生出来的子任务,用于并行或分工。
- `scheduled`
  - 由 job 周期性触发的任务,例如清理、评估、整理。

后续可以继续扩展,但本阶段先围绕这四类把模型、状态和查询入口打通。

## 运行流

一次任务执行里,agent / task 相关链路应该是:

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

这里的边界是:

- `AgentProfileRepo` 管 agent profile 数据。
- `TaskRepo` 管 task / task run 状态。
- `JobRepo` 管定时任务配置与最近运行记录。
- `AIAgent` 只负责执行,不负责保存复杂任务状态模型。
- `delegation` 负责创建和关联子任务,不直接承担持久化职责。

## 管理流

agent / task 的管理入口分三层:

```text
User
  -> CLI / Desktop UI
  -> Sidecar RPC
  -> AgentService / TaskService / JobService
  -> Repo layer
  -> SQLite DB
```

按功能拆开看:

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

## 后台执行

本阶段允许做最小后台执行,但只做"可见、可查、可取消"的基础设施,不做复杂分布式队列。

后台执行至少要支持:

- 创建后台 task 后,状态从 `queued` 进入 `running`。
- 任务完成后进入 `completed`,失败后进入 `failed`,取消后进入 `cancelled`。
- 任务暂停后可进入 `paused`,恢复后产生新的 `task run` 或继续原 run。
- 后台执行结果必须能被 CLI / sidecar / UI 查询。

后台执行的约束是:

- 默认先基于单机本地运行时,不提前引入远程 worker。
- 默认不做复杂优先级抢占,只保留状态与顺序语义。
- 默认不把所有中间过程都提升成 task,只有有明确 owner 和状态需求的执行才建 task。
- 如果任务不能安全恢复,就必须显式标记为不可恢复。

## Delegation

本阶段对 delegation 的要求是:

- 父任务可以创建子任务。
- 子任务必须记录 `parent_task_id`。
- 父任务可以查询子任务状态汇总。
- 子任务产物和日志要能回链到父任务。
- 如果 delegation 失败,失败信息要落在 task / run 层,而不是只留在临时输出里。

先把 delegation 作为 task graph 的最小能力做起来,再考虑更复杂的多 agent 协商。

## 定时任务

scheduled job 至少要支持:

- job definition
  - 名称、目标、周期、启用状态。
- recent runs
  - 最近一次开始时间、结束时间、状态、错误摘要。
- manual trigger
  - 允许手工立刻运行一次。

建议第一批 job 只覆盖:

- periodic cleanup
- periodic curation
- periodic eval

## 状态模型

建议最小 task 状态机为:

- `queued`
- `running`
- `paused`
- `completed`
- `failed`
- `cancelled`

建议最小 job 状态字段为:

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
- 后台任务状态可被查询,不再只是一次性运行。
- job 的最近运行结果可以被查看。
- 现有 `prompt`、`context`、`memory`、`tool`、`provider` 链路不回退。

## 验收方法

先跑自动化测试,再做手工演示。

### 前置准备

从仓库根目录运行:

```powershell
uv sync
```

需要为 CLI 演示准备一个干净的本地数据库时,先清理上次的 demo DB:

```powershell
Remove-Item .tmp\chariot-a6-demo.db -ErrorAction SilentlyContinue
```

### 自动化回归

当前 A6 覆盖集中在这几个测试套件:

```powershell
uv run pytest tests/platform/test_foundations.py -q
uv run pytest tests/sidecar/test_task_methods.py -q
uv run pytest tests/cli/test_task_commands.py tests/cli/test_commands.py -q
```

合并成一条:

```powershell
uv run pytest tests/platform/test_foundations.py tests/sidecar/test_task_methods.py tests/cli/test_task_commands.py tests/cli/test_commands.py -q
```

期望:全部通过。覆盖 agent CRUD、task CRUD、task run 生命周期、delegation、job CRUD 与手工触发。

### CLI 手工演示

用独立的 DB 走 demo,避免污染日常本地状态:

```powershell
$env:CHARIOT_DB_PATH = (Resolve-Path .).Path + "\.tmp\chariot-a6-demo.db"
New-Item -ItemType Directory -Force .tmp | Out-Null
```

#### 1. Agent profile

创建、查看、更新、删除一个 agent profile:

```powershell
uv run chariot agent add --name planner --role planner --tool-profile default --provider-profile mock --budget '{"max_steps": 5}' --meta '{"team":"demo"}'
uv run chariot agent list
uv run chariot agent show planner
uv run chariot agent update planner --role executor --tool-profile default --meta '{"team":"ops"}'
uv run chariot agent show planner
```

验收点:

- `agent list` 能看到 `planner`。
- `agent show planner` 能看到 role、tool profile、provider profile、`budget`、`meta`。
- 执行 `update` 之后,role 变成 `executor`。

#### 2. Task 与 task run

创建一个 task,走一遍 run 生命周期:

```powershell
uv run chariot task create --goal "prepare release checklist" --agent-profile planner --owner user --meta '{"source":"demo"}'
uv run chariot task list
```

从输出里挑一个 task id,然后:

```powershell
uv run chariot task show <task_id>
uv run chariot task start-run <task_id> --trigger manual --meta '{"source":"demo"}'
uv run chariot task runs <task_id>
uv run chariot task show-run <run_id>
uv run chariot task fail-run <run_id> --error "demo failure"
uv run chariot task show <task_id>

uv run chariot task show 01KRAHMCQQXBWG8YNNPFT51KA0
uv run chariot task start-run 01KRAHMCQQXBWG8YNNPFT51KA0 --trigger manual --meta '{"source":"demo"}'
uv run chariot task runs 01KRAHMCQQXBWG8YNNPFT51KA0
uv run chariot task show-run 01KRAHMCQQXBWG8YNNPFT51KA0
uv run chariot task fail-run 01KRAHMCQQXBWG8YNNPFT51KA0 --error "demo failure"
uv run chariot task show 01KRAHMCQQXBWG8YNNPFT51KA0
```

再建第二个 task,走 cancel 路径:

```powershell
uv run chariot task create --goal "cancel me" --agent-profile planner
uv run chariot task start-run <task_id_2>
uv run chariot task cancel-run <run_id_2> --error "user requested"
uv run chariot task show <task_id_2>
```

再建第三个 task,走 pause / resume / complete 路径:

```powershell
uv run chariot task create --goal "complete me" --agent-profile planner
uv run chariot task start-run <task_id_3>
uv run chariot task pause <task_id_3>
uv run chariot task resume <task_id_3>
uv run chariot task complete-run <run_id_3> --result '{"ok": true}'
uv run chariot task show <task_id_3>
```

验收点:

- `task runs` 能列出该 task 的 run 行。
- `show-run` 返回 trigger、status、result、error、时间戳。
- `fail-run` 把 run 和 task 同时推到 `failed`。
- `cancel-run` 把 run 和 task 同时推到 `cancelled`。
- `complete-run` 把 run 和 task 同时推到 `completed`。

#### 3. Delegation

创建一个父任务并派生子任务:

```powershell
uv run chariot task create --goal "parent orchestration" --agent-profile planner
uv run chariot task delegate <parent_task_id> --goal "child a" --goal "child b" --reason "split work" --meta '{"batch":"demo"}'
uv run chariot task list --parent-task-id <parent_task_id>
uv run chariot task show <parent_task_id>
```

验收点:

- `task list --parent-task-id ...` 返回子任务。
- `task show` 父任务时能看到 `child status summary`。
- 每个子任务的 `parent_task_id` 等于 `<parent_task_id>`。

#### 4. 定时任务

创建、查看、更新、手工触发、禁用、启用、删除一个 job:

```powershell
uv run chariot job add --name cleanup --goal "cleanup stale state" --cron "0 * * * *" --agent-profile planner --meta '{"scope":"demo"}'
uv run chariot job list
uv run chariot job show cleanup
uv run chariot job update cleanup --goal "cleanup tmp files" --cron "*/5 * * * *"
uv run chariot job run-now cleanup
uv run chariot job show cleanup
uv run chariot job disable cleanup
uv run chariot job enable cleanup
uv run chariot job remove cleanup
```

验收点:

- `job list` 能看到 `cleanup`。
- `job show cleanup` 显示 `goal`、`cron`、`enabled`、`meta`、`runs`。
- `job run-now cleanup` 同时建出一个 scheduled task 和一条 job run 记录。
- `run-now` 后,`last_run_status` 变成 `queued`。
- `job remove cleanup` 删除 job 定义。

#### 5. 清理

演示结束:

```powershell
uv run chariot agent remove planner
Remove-Item Env:CHARIOT_DB_PATH -ErrorAction SilentlyContinue
```

### Sidecar RPC 烟测

如果要直接走 RPC 而不是 CLI 验证,目前对外的方法分组如下:

- Agent
  - `list_agents`
  - `get_agent`
  - `create_agent`
  - `update_agent`
  - `delete_agent`
- Task
  - `list_tasks`
  - `get_task`
  - `create_task`
  - `pause_task`
  - `resume_task`
  - `cancel_task`
  - `start_task_run`
  - `complete_task_run`
  - `fail_task_run`
  - `cancel_task_run`
  - `get_task_run`
  - `list_task_runs`
  - `delegate_task`
- Job
  - `list_jobs`
  - `show_job`
  - `create_job`
  - `update_job`
  - `enable_job`
  - `disable_job`
  - `delete_job`
  - `run_job_now`

sidecar 回归测试源:

```powershell
uv run pytest tests/sidecar/test_task_methods.py -q
```

### 手工验收重点

- agent 列表能否反映 role、profile 和预算等基础属性。
- `task show` 是否能看清目标、状态、父子关系和最近 run。
- `pause / resume / cancel` 是否只影响 task 层,不污染 prompt / provider 等别的层。
- background task 和 scheduled job 的状态是否能在界面或 RPC 里看出来。
- 如果 delegation 失败,日志或任务详情里能否看出原因。

## 当前边界

A6 已经具备的:

- `agent profile`、`task`、`task run`、`delegation`、`scheduled job` 的完整最小管理面。
- 主要生命周期对象都有 SQLite 持久化。
- CLI 和 sidecar RPC 两条 surface 都可以检视并手工控制这些对象。

A6 还没有给出的:

- 真正常驻的后台 worker,可以自动 claim 队列任务并执行。
- 自动调度 loop,根据 cron 表达式自动触发 job。
- 完整的 checkpoint / resume 恢复能力,当前只有手工 run 切换。
- 风险任务或 delegation 的审批拦截。
- 更完整的 artifact 持久化与浏览;这部分留给后续 artifact milestone。
- 高级多 agent 自治,例如协商、动态重规划、分布式 worker 池。

## 剩余工作

当前 A6 实现之后,主要的未完成项是:

- 后台执行 loop
  - 在 sidecar 或 runtime 内加一个 worker,自动 claim 队列任务、推进 run、收敛终态。
- 自动调度
  - 加一个 scheduler,扫描启用的 job,解析 `cron`,自动调用 `run_job_now`。
- 更好的恢复机制
  - 落库足够多的 checkpoint 元数据,支持 restart-safe 的 resume,而不仅仅是手工切 run 状态。
- Delegation 诊断
  - 在 task / task run 记录里把 delegation 失败原因和血缘信息更显式地暴露出来。
- 审批与策略
  - 给高风险 task、delegation、高成本 profile 加可选的 approval 状态。
- Artifact 接入
  - 把 task 输出挂到正式的 artifact 模型上,而不是只把 result payload 当唯一持久产物。

如果继续走 backend-first,建议顺序是:

1. 后台 worker loop。
2. 在现有 `scheduled_jobs` / `job_runs` 表之上加自动调度。
3. 加执行 checkpoint 与 resume 元数据,支持崩溃恢复。
4. 加强 delegation 诊断与审批钩子。
5. artifact milestone 启动时再接入 artifact。

## 当前约束

- 只做当前阶段需要的最小边界,不提前把复杂自治系统、分布式调度、跨机器恢复做进来。
- 新计划必须先得到确认,再覆盖本文件。
- 归档时只做 verbatim copy,不做内容重写。

# 任务管理演示

> 范围:`Milestone A6: Agent and task management`
> 目标:为 `agent profile`、`task`、`task run`、`delegation`、`scheduled job` 提供一份可直接复制粘贴的测试与演示流程。

## 前置准备

从仓库根目录运行:

```powershell
uv sync
```

若需要为 CLI 演示准备一个干净的本地数据库,先清掉上次的 demo DB:

```powershell
Remove-Item .tmp\chariot-a6-demo.db -ErrorAction SilentlyContinue
```

## 自动化检查

当前 A6 覆盖集中在这几个测试套件:

```powershell
uv run pytest tests/platform/test_foundations.py -q
uv run pytest tests/sidecar/test_task_methods.py -q
uv run pytest tests/cli/test_task_commands.py tests/cli/test_commands.py -q
```

如果想用一条命令跑主回归切片:

```powershell
uv run pytest tests/platform/test_foundations.py tests/sidecar/test_task_methods.py tests/cli/test_task_commands.py tests/cli/test_commands.py -q
```

期望结果:

- 全部用例通过。
- 套件覆盖 agent CRUD、task CRUD、task run 生命周期、delegation、job CRUD,以及 job 手工触发。

## CLI 演示

用独立的 DB 走演示,避免污染日常本地状态:

```powershell
$env:CHARIOT_DB_PATH = (Resolve-Path .).Path + "\.tmp\chariot-a6-demo.db"
New-Item -ItemType Directory -Force .tmp | Out-Null
```

### 1. Agent profile

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

### 2. Task 与 task run

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

### 3. Delegation

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

### 4. 定时任务

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

### 5. Agent 清理

演示结束:

```powershell
uv run chariot agent remove planner
Remove-Item Env:CHARIOT_DB_PATH -ErrorAction SilentlyContinue
```

## Sidecar RPC 烟测

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

## 预期的 A6 产出

跑完本指南后,应能验证以下事项:

- agent profile 不再是静态配置片段,而是可查询、可管理的对象。
- task 可以被创建、检视、暂停、恢复、取消、派生。
- task run 可以被列出和单独检视。
- run 终态路径明确:complete、fail、cancel。
- scheduled job 是可管理的对象,而不是写死的定时器想法。
- 手工触发同时建出一条 scheduled task 和一条 job run 记录。

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

## 推荐的后端推进顺序

如果继续走 backend-first,优先级最高的顺序是:

1. 加后台 worker loop。
2. 在现有 `scheduled_jobs` / `job_runs` 表之上加自动调度。
3. 加执行 checkpoint 与 resume 元数据,支持崩溃恢复。
4. 加强 delegation 诊断与审批钩子。
5. artifact milestone 启动时再接入 artifact。

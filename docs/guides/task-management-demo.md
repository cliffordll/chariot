# Task Management Demo

> Scope: `Milestone A6: Agent and task management`
> Goal: provide a copy-pasteable test and demo flow for `agent profile`, `task`, `task run`, `delegation`, and `scheduled job`.

## Prerequisites

Run from repo root:

```powershell
uv sync
```

If you want a clean local database for the CLI demo, remove the previous demo DB first:

```powershell
Remove-Item .tmp\chariot-a6-demo.db -ErrorAction SilentlyContinue
```

## Automated Checks

Current A6 coverage is concentrated in these suites:

```powershell
uv run pytest tests/platform/test_foundations.py -q
uv run pytest tests/sidecar/test_task_methods.py -q
uv run pytest tests/cli/test_task_commands.py tests/cli/test_commands.py -q
```

If you want the main regression slice in one command:

```powershell
uv run pytest tests/platform/test_foundations.py tests/sidecar/test_task_methods.py tests/cli/test_task_commands.py tests/cli/test_commands.py -q
```

Expected result:

- All tests pass.
- The suite covers agent CRUD, task CRUD, task run lifecycle, delegation, job CRUD, and manual job trigger.

## CLI Demo

Use a dedicated DB so the demo does not pollute your normal local state:

```powershell
$env:CHARIOT_DB_PATH = (Resolve-Path .).Path + "\.tmp\chariot-a6-demo.db"
New-Item -ItemType Directory -Force .tmp | Out-Null
```

### 1. Agent profile

Create, inspect, update, and remove an agent profile:

```powershell
uv run chariot agent add --name planner --role planner --tool-profile default --provider-profile mock --budget '{"max_steps": 5}' --meta '{"team":"demo"}'
uv run chariot agent list
uv run chariot agent show planner
uv run chariot agent update planner --role executor --tool-profile default --meta '{"team":"ops"}'
uv run chariot agent show planner
```

Checkpoints:

- `agent list` shows `planner`
- `agent show planner` shows role, tool profile, provider profile, `budget`, and `meta`
- after `update`, role becomes `executor`

### 2. Task and task runs

Create a task and walk through its run lifecycle:

```powershell
uv run chariot task create --goal "prepare release checklist" --agent-profile planner --owner user --meta '{"source":"demo"}'
uv run chariot task list
```

Pick the task id from the output, then:

```powershell
uv run chariot task show <task_id>
uv run chariot task start-run <task_id> --trigger manual --meta '{"source":"demo"}'
uv run chariot task runs <task_id>
uv run chariot task show-run <run_id>
uv run chariot task fail-run <run_id> --error "demo failure"
uv run chariot task show <task_id>
```

Create a second task to exercise cancel flow:

```powershell
uv run chariot task create --goal "cancel me" --agent-profile planner
uv run chariot task start-run <task_id_2>
uv run chariot task cancel-run <run_id_2> --error "user requested"
uv run chariot task show <task_id_2>
```

Create a third task to exercise pause/resume/complete:

```powershell
uv run chariot task create --goal "complete me" --agent-profile planner
uv run chariot task start-run <task_id_3>
uv run chariot task pause <task_id_3>
uv run chariot task resume <task_id_3>
uv run chariot task complete-run <run_id_3> --result '{"ok": true}'
uv run chariot task show <task_id_3>
```

Checkpoints:

- `task runs` lists the run rows for the task
- `show-run` returns trigger, status, result, error, and timestamps
- `fail-run` moves both run and task to `failed`
- `cancel-run` moves both run and task to `cancelled`
- `complete-run` moves both run and task to `completed`

### 3. Delegation

Create a parent task and delegate children from it:

```powershell
uv run chariot task create --goal "parent orchestration" --agent-profile planner
uv run chariot task delegate <parent_task_id> --goal "child a" --goal "child b" --reason "split work" --meta '{"batch":"demo"}'
uv run chariot task list --parent-task-id <parent_task_id>
uv run chariot task show <parent_task_id>
```

Checkpoints:

- `task list --parent-task-id ...` returns the child tasks
- `task show` on the parent includes `child status summary`
- each child task has `parent_task_id = <parent_task_id>`

### 4. Scheduled jobs

Create, inspect, update, trigger, disable, enable, and remove a job:

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

Checkpoints:

- `job list` shows `cleanup`
- `job show cleanup` shows `goal`, `cron`, `enabled`, `meta`, and `runs`
- `job run-now cleanup` creates a scheduled task and a job run record
- after `run-now`, `last_run_status` becomes `queued`
- `job remove cleanup` deletes the job definition

### 5. Agent cleanup

When the demo is done:

```powershell
uv run chariot agent remove planner
Remove-Item Env:CHARIOT_DB_PATH -ErrorAction SilentlyContinue
```

## Sidecar RPC Smoke

If you want to validate the same surfaces through RPC rather than CLI, these are the main method groups now exposed:

- Agent:
  - `list_agents`
  - `get_agent`
  - `create_agent`
  - `update_agent`
  - `delete_agent`
- Task:
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
- Job:
  - `list_jobs`
  - `show_job`
  - `create_job`
  - `update_job`
  - `enable_job`
  - `disable_job`
  - `delete_job`
  - `run_job_now`

The sidecar regression source of truth is:

```powershell
uv run pytest tests/sidecar/test_task_methods.py -q
```

## Expected A6 Outcome

After running this guide, you should be able to verify all of the following:

- agent profiles are no longer static config fragments; they are queryable and manageable objects
- tasks can be created, inspected, paused, resumed, cancelled, and delegated
- task runs can be listed and individually inspected
- run termination paths are explicit: complete, fail, cancel
- scheduled jobs are manageable objects, not just hard-coded timer ideas
- manual trigger creates both a scheduled task and a job run record

## Current Boundary

What A6 now gives you:

- a complete minimum management surface for `agent profile`, `task`, `task run`, `delegation`, and `scheduled job`
- persistent records in SQLite for the main lifecycle objects
- CLI and sidecar RPC surfaces that let you inspect and manually control those objects

What A6 does not yet give you:

- a real long-running background worker that automatically picks queued tasks and executes them
- an automatic scheduler loop that evaluates cron expressions and fires jobs on its own
- full checkpoint/resume recovery for task execution beyond the current minimal manual run controls
- approval gates for sensitive task transitions or delegation actions
- richer artifact persistence and artifact browsing; that belongs with the later artifact milestone
- advanced multi-agent autonomy such as negotiation, dynamic replanning, or distributed worker pools

## Remaining Work

The main unfinished items after the current A6 implementation are:

- Background execution loop:
  - add a worker inside sidecar or runtime that claims queued tasks, starts runs, and updates terminal state
- Automatic scheduled execution:
  - add a scheduler that scans enabled jobs, evaluates `cron`, and calls `run_job_now` automatically
- Better execution recovery:
  - persist enough checkpoint metadata to support restart-safe resume, not only manual run state toggles
- Delegation diagnostics:
  - make delegation failure reasons and lineage inspection more explicit in `task` and `task run` records
- Approval and policy controls:
  - add optional approval state for risky tasks, delegation, or high-cost profiles
- Artifact integration:
  - attach task outputs to a formal artifact model instead of leaving result payloads as the only durable output

## Recommended Next Backend Steps

If you continue backend-first, the highest-value order is:

1. Add the background worker loop.
2. Add automatic job scheduling on top of the existing `scheduled_jobs` and `job_runs` tables.
3. Add execution checkpoint and resume metadata for crash-safe recovery.
4. Tighten delegation diagnostics and approval hooks.
5. Integrate artifacts when the artifact milestone starts.

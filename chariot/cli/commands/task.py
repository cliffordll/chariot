"""`chariot agent/task/job` commands for Milestone A6."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

import typer

from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.delegation.models import DelegatedTaskSpec, DelegationRequest
from chariot.delegation.service import DelegationService
from chariot.repos.task_repo import TaskRepo
from chariot.tasks.models import TaskCreate, TaskKind, TaskRunCreate
from chariot.tasks.service import AgentService, JobService, TaskService

agent_app = typer.Typer(name="agent", help="管理 agent profiles", no_args_is_help=True)
task_app = typer.Typer(name="task", help="管理 tasks / task runs / delegation", no_args_is_help=True)
job_app = typer.Typer(name="job", help="查看 scheduled jobs", no_args_is_help=True)


def _fmt_dt(value: Any) -> str:
    if value is None:
        return "-"
    if hasattr(value, "isoformat"):
        return value.isoformat(timespec="seconds")
    return str(value)


def _parse_meta(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        Renderer.die(f"meta must be valid JSON: {exc}")
        raise SystemExit(1)
    if not isinstance(data, dict):
        Renderer.die("meta must be a JSON object")
        raise SystemExit(1)
    return data


@agent_app.command("list", help="列出 agent profiles")
def agent_list_cmd() -> None:
    asyncio.run(_agent_list())


@agent_app.command("show", help="查看单个 agent profile")
def agent_show_cmd(
    name: Annotated[str, typer.Argument(help="agent profile name")],
) -> None:
    asyncio.run(_agent_show(name))


@agent_app.command("add", help="创建 agent profile")
def agent_add_cmd(
    name: Annotated[str, typer.Option("--name", help="agent profile name")] = "",
    role: Annotated[str, typer.Option("--role", help="agent role")] = "",
    prompt_bundle: Annotated[str, typer.Option("--prompt-bundle", help="prompt bundle")] = "",
    tool_profile: Annotated[str, typer.Option("--tool-profile", help="tool profile")] = "",
    provider_profile: Annotated[str, typer.Option("--provider-profile", help="provider profile")] = "",
    budget: Annotated[str, typer.Option("--budget", help="JSON budget")] = "",
    meta: Annotated[str, typer.Option("--meta", help="JSON meta")] = "",
) -> None:
    asyncio.run(
        _agent_add(
            name=name,
            role=role,
            prompt_bundle=prompt_bundle or None,
            tool_profile=tool_profile or None,
            provider_profile=provider_profile or None,
            budget=budget,
            meta=meta,
        )
    )


@agent_app.command("update", help="update agent profile")
def agent_update_cmd(
    name: Annotated[str, typer.Argument(help="agent profile name")],
    role: Annotated[str, typer.Option("--role", help="agent role")] = "",
    prompt_bundle: Annotated[str, typer.Option("--prompt-bundle", help="prompt bundle")] = "",
    tool_profile: Annotated[str, typer.Option("--tool-profile", help="tool profile")] = "",
    provider_profile: Annotated[str, typer.Option("--provider-profile", help="provider profile")] = "",
    budget: Annotated[str, typer.Option("--budget", help="JSON budget")] = "",
    meta: Annotated[str, typer.Option("--meta", help="JSON meta")] = "",
) -> None:
    asyncio.run(
        _agent_update(
            name=name,
            role=role or None,
            prompt_bundle=prompt_bundle or None,
            tool_profile=tool_profile or None,
            provider_profile=provider_profile or None,
            budget=budget,
            meta=meta,
        )
    )


@agent_app.command("remove", help="remove agent profile")
def agent_remove_cmd(
    name: Annotated[str, typer.Argument(help="agent profile name")],
) -> None:
    asyncio.run(_agent_remove(name))


async def _agent_list() -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        entries = await AgentService(TaskRepo(session)).list_agents()
    if not entries:
        Renderer.out("(没有 agent profiles)")
        return
    rows = [
        (
            entry.name,
            entry.role,
            entry.prompt_bundle or "-",
            entry.tool_profile or "-",
            entry.provider_profile or "-",
        )
        for entry in entries
    ]
    Renderer.table(["name", "role", "prompt_bundle", "tool_profile", "provider_profile"], rows, title="agents")


async def _agent_show(name: str) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        entry = await AgentService(TaskRepo(session)).get_agent(name)
        if entry is None:
            Renderer.die(f"agent profile not found: {name!r}")
            return
    Renderer.kv(
        {
            "name": entry.name,
            "role": entry.role,
            "prompt_bundle": entry.prompt_bundle or "-",
            "tool_profile": entry.tool_profile or "-",
            "provider_profile": entry.provider_profile or "-",
            "created_at": _fmt_dt(entry.created_at),
            "updated_at": _fmt_dt(entry.updated_at),
        }
    )
    Renderer.out("")
    Renderer.out("budget:")
    Renderer.out(json.dumps(entry.budget, ensure_ascii=False, indent=2, sort_keys=True))
    Renderer.out("")
    Renderer.out("meta:")
    Renderer.out(json.dumps(entry.meta, ensure_ascii=False, indent=2, sort_keys=True))


async def _agent_add(
    *,
    name: str,
    role: str,
    prompt_bundle: str | None,
    tool_profile: str | None,
    provider_profile: str | None,
    budget: str,
    meta: str,
) -> None:
    if not name.strip() or not role.strip():
        Renderer.die("--name and --role are required")
        return
    parsed_budget = _parse_meta(budget) or {}
    parsed_meta = _parse_meta(meta) or {}
    async with installed_runtime() as agent, agent.session_maker() as session:
        entry = await TaskRepo(session).create_agent_profile(
            name=name.strip(),
            role=role.strip(),
            prompt_bundle=prompt_bundle,
            tool_profile=tool_profile,
            provider_profile=provider_profile,
            budget=parsed_budget,
            meta=parsed_meta,
        )
    Renderer.out(f"+ {entry.name} {entry.role}")


async def _agent_update(
    *,
    name: str,
    role: str | None,
    prompt_bundle: str | None,
    tool_profile: str | None,
    provider_profile: str | None,
    budget: str,
    meta: str,
) -> None:
    parsed_budget = _parse_meta(budget) if budget.strip() else None
    parsed_meta = _parse_meta(meta) if meta.strip() else None
    async with installed_runtime() as agent, agent.session_maker() as session:
        service = AgentService(TaskRepo(session))
        try:
            entry = await service.update_agent(
                name=name,
                role=role,
                prompt_bundle=prompt_bundle,
                tool_profile=tool_profile,
                provider_profile=provider_profile,
                budget=parsed_budget,
                meta=parsed_meta,
            )
        except ValueError as exc:
            Renderer.die(str(exc))
            return
    Renderer.out(f"~ {entry.name} {entry.role}")


async def _agent_remove(name: str) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        service = AgentService(TaskRepo(session))
        try:
            await service.delete_agent(name)
        except ValueError as exc:
            Renderer.die(str(exc))
            return
    Renderer.out(f"- {name}")


@task_app.command("list", help="列出 tasks")
def task_list_cmd(
    parent_task_id: Annotated[str, typer.Option("--parent-task-id", help="只看某个父任务的 children")] = "",
) -> None:
    asyncio.run(_task_list(parent_task_id or None))


@task_app.command("show", help="查看单个 task")
def task_show_cmd(
    task_id: Annotated[str, typer.Argument(help="task id")],
) -> None:
    asyncio.run(_task_show(task_id))


@task_app.command("create", help="创建 task")
def task_create_cmd(
    goal: Annotated[str, typer.Option("--goal", help="task goal")] = "",
    kind: Annotated[str, typer.Option("--kind", help="interactive/background/delegated/scheduled")] = "interactive",
    agent_profile: Annotated[str, typer.Option("--agent-profile", help="agent profile name")] = "",
    owner: Annotated[str, typer.Option("--owner", help="task owner")] = "",
    meta: Annotated[str, typer.Option("--meta", help="JSON meta")] = "",
) -> None:
    asyncio.run(
        _task_create(
            goal=goal,
            kind=kind,
            agent_profile=agent_profile or None,
            owner=owner or None,
            meta=meta,
        )
    )


@task_app.command("pause", help="暂停 task")
def task_pause_cmd(
    task_id: Annotated[str, typer.Argument(help="task id")],
) -> None:
    asyncio.run(_task_transition(task_id, "pause"))


@task_app.command("resume", help="恢复 task")
def task_resume_cmd(
    task_id: Annotated[str, typer.Argument(help="task id")],
) -> None:
    asyncio.run(_task_transition(task_id, "resume"))


@task_app.command("cancel", help="取消 task")
def task_cancel_cmd(
    task_id: Annotated[str, typer.Argument(help="task id")],
) -> None:
    asyncio.run(_task_transition(task_id, "cancel"))


@task_app.command("delegate", help="派发子任务")
def task_delegate_cmd(
    parent_task_id: Annotated[str, typer.Argument(help="parent task id")],
    goal: Annotated[list[str], typer.Option("--goal", help="child task goal; repeatable")] = [],
    reason: Annotated[str, typer.Option("--reason", help="delegation reason")] = "",
    agent_profile: Annotated[str, typer.Option("--agent-profile", help="apply to all child tasks")] = "",
    owner: Annotated[str, typer.Option("--owner", help="apply to all child tasks")] = "",
    meta: Annotated[str, typer.Option("--meta", help="JSON meta for delegation")] = "",
    ) -> None:
    asyncio.run(
        _task_delegate(
            parent_task_id=parent_task_id,
            goals=goal,
            reason=reason or None,
            agent_profile=agent_profile or None,
            owner=owner or None,
            meta=meta,
        )
    )


@task_app.command("start-run", help="为 task 启动一条 run")
def task_start_run_cmd(
    task_id: Annotated[str, typer.Argument(help="task id")],
    trigger: Annotated[str, typer.Option("--trigger", help="run trigger")] = "manual",
    resume_from_run_id: Annotated[str, typer.Option("--resume-from-run-id", help="resume from previous run")] = "",
    meta: Annotated[str, typer.Option("--meta", help="JSON meta")] = "",
) -> None:
    asyncio.run(
        _task_start_run(
            task_id=task_id,
            trigger=trigger,
            resume_from_run_id=resume_from_run_id or None,
            meta=meta,
        )
    )


@task_app.command("runs", help="list task runs")
def task_runs_cmd(
    task_id: Annotated[str, typer.Argument(help="task id")],
) -> None:
    asyncio.run(_task_runs(task_id))


@task_app.command("show-run", help="show one task run")
def task_show_run_cmd(
    run_id: Annotated[str, typer.Argument(help="run id")],
) -> None:
    asyncio.run(_task_show_run(run_id))


@task_app.command("complete-run", help="完成一条 task run")
def task_complete_run_cmd(
    run_id: Annotated[str, typer.Argument(help="run id")],
    result: Annotated[str, typer.Option("--result", help="JSON result")] = "",
    error: Annotated[str, typer.Option("--error", help="error message")] = "",
) -> None:
    asyncio.run(_task_complete_run(run_id=run_id, result=result, error=error or None))


@task_app.command("fail-run", help="fail a task run")
def task_fail_run_cmd(
    run_id: Annotated[str, typer.Argument(help="run id")],
    error: Annotated[str, typer.Option("--error", help="error message")] = "",
    result: Annotated[str, typer.Option("--result", help="JSON result")] = "",
) -> None:
    asyncio.run(_task_fail_run(run_id=run_id, error=error, result=result))


@task_app.command("cancel-run", help="cancel a task run")
def task_cancel_run_cmd(
    run_id: Annotated[str, typer.Argument(help="run id")],
    error: Annotated[str, typer.Option("--error", help="cancel reason")] = "",
    result: Annotated[str, typer.Option("--result", help="JSON result")] = "",
) -> None:
    asyncio.run(_task_cancel_run(run_id=run_id, error=error or None, result=result))


@job_app.command("list", help="列出 scheduled jobs")
def job_list_cmd() -> None:
    asyncio.run(_job_list())


@job_app.command("show", help="查看单个 job 和最近 runs")
def job_show_cmd(
    name: Annotated[str, typer.Argument(help="job name")],
) -> None:
    asyncio.run(_job_show(name))


@job_app.command("add", help="create scheduled job")
def job_add_cmd(
    name: Annotated[str, typer.Option("--name", help="job name")] = "",
    goal: Annotated[str, typer.Option("--goal", help="job goal")] = "",
    cron: Annotated[str, typer.Option("--cron", help="cron expression")] = "",
    agent_profile: Annotated[str, typer.Option("--agent-profile", help="agent profile name")] = "",
    meta: Annotated[str, typer.Option("--meta", help="JSON meta")] = "",
    disabled: Annotated[bool, typer.Option("--disabled", help="create disabled job")] = False,
) -> None:
    asyncio.run(
        _job_add(
            name=name,
            goal=goal,
            cron=cron,
            agent_profile=agent_profile or None,
            meta=meta,
            enabled=not disabled,
        )
    )


@job_app.command("update", help="update scheduled job")
def job_update_cmd(
    name: Annotated[str, typer.Argument(help="job name")],
    goal: Annotated[str, typer.Option("--goal", help="job goal")] = "",
    cron: Annotated[str, typer.Option("--cron", help="cron expression")] = "",
    agent_profile: Annotated[str, typer.Option("--agent-profile", help="agent profile name")] = "",
    meta: Annotated[str, typer.Option("--meta", help="JSON meta")] = "",
) -> None:
    asyncio.run(
        _job_update(
            name=name,
            goal=goal or None,
            cron=cron or None,
            agent_profile=agent_profile or None,
            meta=meta,
        )
    )


@job_app.command("enable", help="enable scheduled job")
def job_enable_cmd(
    name: Annotated[str, typer.Argument(help="job name")],
) -> None:
    asyncio.run(_job_set_enabled(name, True))


@job_app.command("disable", help="disable scheduled job")
def job_disable_cmd(
    name: Annotated[str, typer.Argument(help="job name")],
) -> None:
    asyncio.run(_job_set_enabled(name, False))


@job_app.command("remove", help="remove scheduled job")
def job_remove_cmd(
    name: Annotated[str, typer.Argument(help="job name")],
) -> None:
    asyncio.run(_job_remove(name))


@job_app.command("run-now", help="立刻触发一次 job")
def job_run_now_cmd(
    name: Annotated[str, typer.Argument(help="job name")],
) -> None:
    asyncio.run(_job_run_now(name))


async def _task_list(parent_task_id: str | None) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        entries = await TaskService(TaskRepo(session)).list_tasks(parent_task_id=parent_task_id)
    if not entries:
        Renderer.out("(没有 tasks)")
        return
    rows = [
        (
            entry.id,
            entry.kind.value,
            entry.status.value,
            entry.agent_profile or "-",
            entry.parent_task_id or "-",
            _truncate(entry.goal, 56),
        )
        for entry in entries
    ]
    Renderer.table(["id", "kind", "status", "agent_profile", "parent_task_id", "goal"], rows, title="tasks")


async def _task_show(task_id: str) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        repo = TaskRepo(session)
        service = TaskService(repo)
        entry = await service.get_task(task_id)
        if entry is None:
            Renderer.die(f"task not found: {task_id!r}")
            return
        runs = await repo.list_runs(task_id)
        child_summary = await service.summarize_child_statuses(task_id)
    Renderer.kv(
        {
            "id": entry.id,
            "goal": entry.goal,
            "kind": entry.kind.value,
            "status": entry.status.value,
            "agent_profile": entry.agent_profile or "-",
            "parent_task_id": entry.parent_task_id or "-",
            "owner": entry.owner or "-",
            "created_at": _fmt_dt(entry.created_at),
            "updated_at": _fmt_dt(entry.updated_at),
        }
    )
    Renderer.out("")
    Renderer.out("meta:")
    Renderer.out(json.dumps(entry.meta, ensure_ascii=False, indent=2, sort_keys=True))
    Renderer.out("")
    Renderer.out("artifacts:")
    Renderer.out(json.dumps(entry.artifacts, ensure_ascii=False, indent=2))
    Renderer.out("")
    Renderer.out("child status summary:")
    Renderer.out(json.dumps(child_summary, ensure_ascii=False, indent=2, sort_keys=True))
    if runs:
        Renderer.out("")
        Renderer.table(
            ["run_id", "status", "trigger", "started_at", "finished_at"],
            [
                (run.id, run.status.value, run.trigger, _fmt_dt(run.started_at), _fmt_dt(run.finished_at))
                for run in runs
            ],
            title="task runs",
        )


async def _task_create(
    *,
    goal: str,
    kind: str,
    agent_profile: str | None,
    owner: str | None,
    meta: str,
) -> None:
    if not goal.strip():
        Renderer.die("--goal is required")
        return
    parsed_meta = _parse_meta(meta) or {}
    try:
        task_kind = TaskKind(kind)
    except ValueError:
        Renderer.die(f"invalid task kind: {kind!r}")
        return
    async with installed_runtime() as agent, agent.session_maker() as session:
        try:
            entry = await TaskService(TaskRepo(session)).create_task(
                TaskCreate(
                    goal=goal,
                    kind=task_kind,
                    agent_profile=agent_profile,
                    owner=owner,
                    meta=parsed_meta,
                )
            )
        except ValueError as exc:
            Renderer.die(str(exc))
            return
    Renderer.out(f"+ {entry.id} {entry.kind.value} {entry.status.value}")


async def _task_transition(task_id: str, action: str) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        service = TaskService(TaskRepo(session))
        try:
            if action == "pause":
                entry = await service.pause_task(task_id)
            elif action == "resume":
                entry = await service.resume_task(task_id)
            elif action == "cancel":
                entry = await service.cancel_task(task_id)
            else:
                Renderer.die(f"unsupported action: {action!r}")
                return
        except ValueError as exc:
            Renderer.die(str(exc))
            return
    Renderer.out(f"~ {entry.id}: {entry.status.value}")


async def _task_start_run(
    *,
    task_id: str,
    trigger: str,
    resume_from_run_id: str | None,
    meta: str,
) -> None:
    parsed_meta = _parse_meta(meta) or {}
    async with installed_runtime() as agent, agent.session_maker() as session:
        service = TaskService(TaskRepo(session))
        try:
            run = await service.start_task_run(
                TaskRunCreate(
                    task_id=task_id,
                    trigger=trigger,
                    resume_from_run_id=resume_from_run_id,
                    meta=parsed_meta,
                )
            )
        except ValueError as exc:
            Renderer.die(str(exc))
            return
    Renderer.out(f"+ run {run.id} {run.status.value} task={run.task_id}")


async def _task_runs(task_id: str) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        service = TaskService(TaskRepo(session))
        try:
            runs = await service.list_task_runs(task_id)
        except ValueError as exc:
            Renderer.die(str(exc))
            return
    if not runs:
        Renderer.out("(no task runs)")
        return
    Renderer.table(
        ["run_id", "status", "trigger", "started_at", "finished_at"],
        [(run.id, run.status.value, run.trigger, _fmt_dt(run.started_at), _fmt_dt(run.finished_at)) for run in runs],
        title="task runs",
    )


async def _task_show_run(run_id: str) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        run = await TaskService(TaskRepo(session)).get_task_run(run_id)
        if run is None:
            Renderer.die(f"task run not found: {run_id!r}")
            return
    Renderer.kv(
        {
            "id": run.id,
            "task_id": run.task_id,
            "status": run.status.value,
            "trigger": run.trigger,
            "resume_from_run_id": run.resume_from_run_id or "-",
            "error": run.error or "-",
            "started_at": _fmt_dt(run.started_at),
            "finished_at": _fmt_dt(run.finished_at),
        }
    )
    Renderer.out("")
    Renderer.out("meta:")
    Renderer.out(json.dumps(run.meta, ensure_ascii=False, indent=2, sort_keys=True))
    Renderer.out("")
    Renderer.out("result:")
    Renderer.out(json.dumps(run.result, ensure_ascii=False, indent=2, sort_keys=True))


async def _task_complete_run(
    *,
    run_id: str,
    result: str,
    error: str | None,
) -> None:
    parsed_result = _parse_meta(result) or {}
    async with installed_runtime() as agent, agent.session_maker() as session:
        service = TaskService(TaskRepo(session))
        try:
            run = await service.complete_task_run(run_id, result=parsed_result, error=error)
        except ValueError as exc:
            Renderer.die(str(exc))
            return
    Renderer.out(f"~ run {run.id}: {run.status.value}")


async def _task_fail_run(
    *,
    run_id: str,
    error: str,
    result: str,
) -> None:
    if not error.strip():
        Renderer.die("--error is required")
        return
    parsed_result = _parse_meta(result) or {}
    async with installed_runtime() as agent, agent.session_maker() as session:
        service = TaskService(TaskRepo(session))
        try:
            run = await service.fail_task_run(run_id, error=error, result=parsed_result)
        except ValueError as exc:
            Renderer.die(str(exc))
            return
    Renderer.out(f"~ run {run.id}: {run.status.value}")


async def _task_cancel_run(
    *,
    run_id: str,
    error: str | None,
    result: str,
) -> None:
    parsed_result = _parse_meta(result) or {}
    async with installed_runtime() as agent, agent.session_maker() as session:
        service = TaskService(TaskRepo(session))
        try:
            run = await service.cancel_task_run(run_id, error=error, result=parsed_result)
        except ValueError as exc:
            Renderer.die(str(exc))
            return
    Renderer.out(f"~ run {run.id}: {run.status.value}")


async def _task_delegate(
    *,
    parent_task_id: str,
    goals: list[str],
    reason: str | None,
    agent_profile: str | None,
    owner: str | None,
    meta: str,
) -> None:
    if not goals:
        Renderer.die("at least one --goal is required")
        return
    parsed_meta = _parse_meta(meta) or {}
    specs = tuple(
        DelegatedTaskSpec(goal=goal, agent_profile=agent_profile, owner=owner, meta={})
        for goal in goals
        if goal.strip()
    )
    if not specs:
        Renderer.die("at least one non-empty --goal is required")
        return
    async with installed_runtime() as agent, agent.session_maker() as session:
        service = DelegationService(TaskService(TaskRepo(session)))
        try:
            result = await service.delegate(
                DelegationRequest(
                    parent_task_id=parent_task_id,
                    tasks=specs,
                    reason=reason,
                    meta=parsed_meta,
                )
            )
        except ValueError as exc:
            Renderer.die(str(exc))
            return
    Renderer.out(f"~ delegated {result.created}/{result.requested} from {result.parent_task_id}")
    for task_id in result.child_task_ids:
        Renderer.out(f"  - {task_id}")


async def _job_list() -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        entries = await JobService(TaskRepo(session), TaskService(TaskRepo(session))).list_jobs()
    if not entries:
        Renderer.out("(没有 jobs)")
        return
    rows = [
        (
            entry.name,
            "yes" if entry.enabled else "no",
            entry.agent_profile or "-",
            entry.cron,
            entry.last_run_status or "-",
        )
        for entry in entries
    ]
    Renderer.table(["name", "enabled", "agent_profile", "cron", "last_run_status"], rows, title="jobs")


async def _job_show(name: str) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        repo = TaskRepo(session)
        service = JobService(repo, TaskService(repo))
        entry = await service.get_job(name)
        if entry is None:
            Renderer.die(f"job not found: {name!r}")
            return
        runs = await service.list_job_runs(name)
    Renderer.kv(
        {
            "name": entry.name,
            "goal": entry.goal,
            "cron": entry.cron,
            "enabled": "yes" if entry.enabled else "no",
            "agent_profile": entry.agent_profile or "-",
            "last_run_status": entry.last_run_status or "-",
            "last_run_at": _fmt_dt(entry.last_run_at),
            "next_run_at": _fmt_dt(entry.next_run_at),
            "created_at": _fmt_dt(entry.created_at),
            "updated_at": _fmt_dt(entry.updated_at),
        }
    )
    Renderer.out("")
    Renderer.out("meta:")
    Renderer.out(json.dumps(entry.meta, ensure_ascii=False, indent=2, sort_keys=True))
    if runs:
        Renderer.out("")
        Renderer.table(
            ["run_id", "task_id", "status", "started_at", "finished_at"],
            [
                (run.id, run.task_id or "-", run.status, _fmt_dt(run.started_at), _fmt_dt(run.finished_at))
                for run in runs
            ],
            title="job runs",
        )


async def _job_add(
    *,
    name: str,
    goal: str,
    cron: str,
    agent_profile: str | None,
    meta: str,
    enabled: bool,
) -> None:
    if not name.strip() or not goal.strip() or not cron.strip():
        Renderer.die("--name, --goal and --cron are required")
        return
    parsed_meta = _parse_meta(meta) or {}
    async with installed_runtime() as agent, agent.session_maker() as session:
        repo = TaskRepo(session)
        service = JobService(repo, TaskService(repo))
        try:
            entry = await service.create_job(
                name=name.strip(),
                goal=goal.strip(),
                cron=cron.strip(),
                enabled=enabled,
                agent_profile=agent_profile,
                meta=parsed_meta,
            )
        except ValueError as exc:
            Renderer.die(str(exc))
            return
    Renderer.out(f"+ {entry.name} {'enabled' if entry.enabled else 'disabled'}")


async def _job_update(
    *,
    name: str,
    goal: str | None,
    cron: str | None,
    agent_profile: str | None,
    meta: str,
) -> None:
    parsed_meta = _parse_meta(meta) if meta.strip() else None
    async with installed_runtime() as agent, agent.session_maker() as session:
        repo = TaskRepo(session)
        service = JobService(repo, TaskService(repo))
        try:
            entry = await service.update_job(
                name=name,
                goal=goal,
                cron=cron,
                agent_profile=agent_profile,
                meta=parsed_meta,
            )
        except ValueError as exc:
            Renderer.die(str(exc))
            return
    Renderer.out(f"~ {entry.name}: cron={entry.cron} enabled={'yes' if entry.enabled else 'no'}")


async def _job_set_enabled(name: str, enabled: bool) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        repo = TaskRepo(session)
        service = JobService(repo, TaskService(repo))
        try:
            entry = await (service.enable_job(name) if enabled else service.disable_job(name))
        except ValueError as exc:
            Renderer.die(str(exc))
            return
    Renderer.out(f"~ {entry.name}: {'enabled' if entry.enabled else 'disabled'}")


async def _job_run_now(name: str) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        repo = TaskRepo(session)
        service = JobService(repo, TaskService(repo))
        try:
            task, run = await service.run_job_now(name)
        except ValueError as exc:
            Renderer.die(str(exc))
            return
    Renderer.out(f"~ job {name} queued task {task.id} (job_run={run.id})")


async def _job_remove(name: str) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        repo = TaskRepo(session)
        service = JobService(repo, TaskService(repo))
        try:
            await service.delete_job(name)
        except ValueError as exc:
            Renderer.die(str(exc))
            return
    Renderer.out(f"- {name}")


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def register(app: typer.Typer) -> None:
    app.add_typer(agent_app)
    app.add_typer(task_app)
    app.add_typer(job_app)

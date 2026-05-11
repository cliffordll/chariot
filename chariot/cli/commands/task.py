"""`chariot task` commands."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

import typer

from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.models.task import (
    DelegatedTaskSpec,
    DelegationRequest,
    TaskCreate,
    TaskKind,
    TaskRunCreate,
)
from chariot.repos.task_repo import TaskRepo
from chariot.services.task import TaskService

task_app = typer.Typer(name="task", help="管理 tasks / task runs / delegation", no_args_is_help=True)


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
        raise SystemExit(1) from exc
    if not isinstance(data, dict):
        Renderer.die("meta must be a JSON object")
        raise SystemExit(1) from None
    return data


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


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
                (
                    run.id,
                    run.status.value,
                    run.trigger,
                    _fmt_dt(run.started_at),
                    _fmt_dt(run.finished_at),
                )
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
        [
            (
                run.id,
                run.status.value,
                run.trigger,
                _fmt_dt(run.started_at),
                _fmt_dt(run.finished_at),
            )
            for run in runs
        ],
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
        service = TaskService(TaskRepo(session))
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


def register(app: typer.Typer) -> None:
    app.add_typer(task_app)

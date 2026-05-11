"""`chariot job` commands."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

import typer

from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.repos.task_repo import TaskRepo
from chariot.services.job import JobService
from chariot.services.task import TaskService

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
        raise SystemExit(1) from exc
    if not isinstance(data, dict):
        Renderer.die("meta must be a JSON object")
        raise SystemExit(1) from None
    return data


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
                (
                    run.id,
                    run.task_id or "-",
                    run.status,
                    _fmt_dt(run.started_at),
                    _fmt_dt(run.finished_at),
                )
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


def register(app: typer.Typer) -> None:
    app.add_typer(job_app)

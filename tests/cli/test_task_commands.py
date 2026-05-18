from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from chariot.cli.__main__ import app
from chariot.models.task import TaskCreate, TaskRunCreate
from chariot.repos.task_repo import TaskRepo
from chariot.services.task import TaskService

runner = CliRunner()
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI_RE.sub("", text)


@pytest.fixture(autouse=True)
def _wide_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chariot.cli import _runtime

    monkeypatch.setattr(_runtime, "DEFAULT_DB_PATH", tmp_path / "chariot.db")


def test_agent_task_job_help_groups_present() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    for sub in ("agent", "task", "job"):
        assert sub in out


def test_task_help_contains_create_show_pause_resume_cancel_delegate() -> None:
    result = runner.invoke(app, ["task", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    for sub in ("list", "show", "create", "pause", "resume", "cancel", "delegate"):
        assert sub in out


def test_agent_list_empty() -> None:
    result = runner.invoke(app, ["agent", "list"])
    assert result.exit_code == 0
    assert "没有 agent profiles" in _plain(result.output)


def test_agent_add_and_show() -> None:
    result = runner.invoke(
        app,
        [
            "agent",
            "add",
            "--name",
            "planner",
            "--role",
            "planner",
            "--toolset-id",
            "default",
            "--provider-id",
            "mock",
            "--budget",
            '{"max_steps": 5}',
        ],
    )
    assert result.exit_code == 0
    assert "+ planner planner" in _plain(result.output)

    result = runner.invoke(app, ["agent", "show", "planner"])
    assert result.exit_code == 0
    out = _plain(result.output)
    assert "planner" in out
    assert "max_steps" in out


def test_agent_update_and_remove() -> None:
    result = runner.invoke(app, ["agent", "add", "--name", "planner", "--role", "planner"])
    assert result.exit_code == 0

    result = runner.invoke(app, ["agent", "update", "planner", "--role", "executor", "--toolset-id", "default"])
    assert result.exit_code == 0
    assert "~ planner executor" in _plain(result.output)

    result = runner.invoke(app, ["agent", "remove", "planner"])
    assert result.exit_code == 0
    assert "- planner" in _plain(result.output)


def test_task_create_and_list() -> None:
    result = runner.invoke(app, ["task", "create", "--goal", "plan next step"])
    assert result.exit_code == 0
    out = _plain(result.output)
    assert "+ " in out and "queued" in out

    result = runner.invoke(app, ["task", "list"])
    assert result.exit_code == 0
    assert "plan next step" in _plain(result.output)


def test_job_list_empty() -> None:
    result = runner.invoke(app, ["job", "list"])
    assert result.exit_code == 0
    assert "没有 jobs" in _plain(result.output)


def test_job_show_and_run_now(tmp_path: Path) -> None:
    from chariot.database.session import dispose_db, init_db

    async def _seed() -> None:
        sm = await init_db(tmp_path / "chariot.db")
        async with sm() as session:
            await TaskRepo(session).create_job(
                name="cleanup",
                goal="cleanup stale state",
                cron="0 * * * *",
            )

    asyncio.run(_seed())

    result = runner.invoke(app, ["job", "show", "cleanup"])
    assert result.exit_code == 0
    assert "cleanup" in _plain(result.output)

    result = runner.invoke(app, ["job", "run-now", "cleanup"])
    assert result.exit_code == 0
    assert "queued task" in _plain(result.output)

    result = runner.invoke(app, ["job", "show", "cleanup"])
    assert result.exit_code == 0
    out = _plain(result.output)
    assert "last_run_status" in out
    assert "queued" in out

    asyncio.run(dispose_db())


def test_job_add_update_enable_disable() -> None:
    result = runner.invoke(
        app,
        [
            "job",
            "add",
            "--name",
            "cleanup",
            "--goal",
            "cleanup stale state",
            "--cron",
            "0 * * * *",
            "--disabled",
            "--meta",
            '{"scope":"repo"}',
        ],
    )
    assert result.exit_code == 0
    assert "+ cleanup disabled" in _plain(result.output)

    result = runner.invoke(app, ["job", "enable", "cleanup"])
    assert result.exit_code == 0
    assert "enabled" in _plain(result.output)

    result = runner.invoke(
        app,
        ["job", "update", "cleanup", "--goal", "cleanup tmp files", "--cron", "*/5 * * * *"],
    )
    assert result.exit_code == 0
    out = _plain(result.output)
    assert "cleanup" in out
    assert "*/5 * * * *" in out

    result = runner.invoke(app, ["job", "show", "cleanup"])
    assert result.exit_code == 0
    out = _plain(result.output)
    assert "cleanup tmp files" in out
    assert "scope" in out

    result = runner.invoke(app, ["job", "disable", "cleanup"])
    assert result.exit_code == 0
    assert "disabled" in _plain(result.output)

    result = runner.invoke(app, ["job", "remove", "cleanup"])
    assert result.exit_code == 0
    assert "- cleanup" in _plain(result.output)


def test_task_show_pause_resume_cancel_and_delegate(tmp_path: Path) -> None:
    from chariot.database.session import dispose_db, init_db

    async def _seed() -> str:
        sm = await init_db(tmp_path / "chariot.db")
        async with sm() as session:
            repo = TaskRepo(session)
            service = TaskService(repo)
            parent = await service.create_task(TaskCreate(goal="parent"))
            await service.start_task_run(TaskRunCreate(task_id=parent.id))
            return parent.id

    parent_id = asyncio.run(_seed())

    result = runner.invoke(app, ["task", "show", parent_id])
    assert result.exit_code == 0
    assert parent_id in _plain(result.output)

    result = runner.invoke(app, ["task", "pause", parent_id])
    assert result.exit_code == 0
    assert "paused" in _plain(result.output)

    result = runner.invoke(app, ["task", "resume", parent_id])
    assert result.exit_code == 0
    assert "running" in _plain(result.output)

    result = runner.invoke(app, ["task", "start-run", parent_id])
    assert result.exit_code == 0
    out = _plain(result.output)
    assert "run " in out and "running" in out
    run_id = out.split()[2]

    result = runner.invoke(app, ["task", "delegate", parent_id, "--goal", "child a", "--goal", "child b"])
    assert result.exit_code == 0
    out = _plain(result.output)
    assert "delegated 2/2" in out

    result = runner.invoke(app, ["task", "list", "--parent-task-id", parent_id])
    assert result.exit_code == 0
    out = _plain(result.output)
    assert "child a" in out
    assert "child b" in out

    result = runner.invoke(app, ["task", "complete-run", run_id, "--result", '{"ok": true}'])
    assert result.exit_code == 0
    assert "completed" in _plain(result.output)

    result = runner.invoke(app, ["task", "cancel", parent_id])
    assert result.exit_code != 0

    asyncio.run(dispose_db())


def test_task_fail_run_and_cancel_run(tmp_path: Path) -> None:
    from chariot.database.session import dispose_db, init_db

    async def _seed() -> tuple[str, str]:
        sm = await init_db(tmp_path / "chariot.db")
        async with sm() as session:
            repo = TaskRepo(session)
            service = TaskService(repo)
            failed_task = await service.create_task(TaskCreate(goal="worker fail"))
            failed_run = await service.start_task_run(TaskRunCreate(task_id=failed_task.id))
            cancelled_task = await service.create_task(TaskCreate(goal="worker cancel"))
            cancelled_run = await service.start_task_run(TaskRunCreate(task_id=cancelled_task.id))
            return failed_run.id, cancelled_run.id

    failed_run_id, cancelled_run_id = asyncio.run(_seed())

    result = runner.invoke(app, ["task", "fail-run", failed_run_id, "--error", "boom"])
    assert result.exit_code == 0
    assert "failed" in _plain(result.output)

    result = runner.invoke(app, ["task", "cancel-run", cancelled_run_id, "--error", "user requested"])
    assert result.exit_code == 0
    assert "cancelled" in _plain(result.output)

    result = runner.invoke(app, ["task", "cancel-run", failed_run_id, "--error", "second attempt"])
    assert result.exit_code != 0

    asyncio.run(dispose_db())


def test_task_runs_and_show_run(tmp_path: Path) -> None:
    from chariot.database.session import dispose_db, init_db

    async def _seed() -> tuple[str, str]:
        sm = await init_db(tmp_path / "chariot.db")
        async with sm() as session:
            repo = TaskRepo(session)
            service = TaskService(repo)
            task = await service.create_task(TaskCreate(goal="worker"))
            run = await service.start_task_run(TaskRunCreate(task_id=task.id))
            return task.id, run.id

    task_id, run_id = asyncio.run(_seed())

    result = runner.invoke(app, ["task", "runs", task_id])
    assert result.exit_code == 0
    assert run_id in _plain(result.output)

    result = runner.invoke(app, ["task", "show-run", run_id])
    assert result.exit_code == 0
    out = _plain(result.output)
    assert task_id in out
    assert "running" in out

    asyncio.run(dispose_db())

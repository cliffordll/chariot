from __future__ import annotations

from collections.abc import AsyncIterator
from importlib import import_module
from pathlib import Path

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.audit.repo import AuditRepo
from chariot.checkpoints.repo import CheckpointRepo
from chariot.database.session import dispose_db, init_db
from chariot.memory.repo import MemoryRepo
from chariot.skills.repo import SkillRepo

EvalRepo = import_module("chariot.eval.repo").EvalRepo


@pytest_asyncio.fixture
async def session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    db_path = tmp_path / "chariot.db"
    sm = await init_db(db_path)
    async with sm() as session:
        yield session
    await dispose_db()


class TestMigrationV8:
    async def test_platform_tables_exist(self, session: AsyncSession) -> None:
        names = {
            "memories",
            "eval_runs",
            "eval_cases",
            "audit_events",
            "checkpoints",
            "skills",
        }
        rows = (
            await session.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            )
        ).scalars().all()
        assert names.issubset(set(rows))


class TestPlatformRepos:
    async def test_memory_repo_create_and_list(self, session: AsyncSession) -> None:
        repo = MemoryRepo(session)
        entry = await repo.create(kind="preference", text="默认用中文", meta={"scope": "user"})
        assert len(entry.id) == 26
        listed = await repo.list_entries()
        assert listed[0].id == entry.id
        assert listed[0].meta["scope"] == "user"

    async def test_eval_repo_create_run_and_case(self, session: AsyncSession) -> None:
        repo = EvalRepo(session)
        run = await repo.create_run(name="smoke", status="ok", summary={"passed": 1})
        case = await repo.create_case(
            suite="smoke",
            name="basic",
            input_payload={"prompt": "hi"},
            expected={"contains": "hello"},
        )
        assert (await repo.get_run(run.id)) is not None
        assert len(run.id) == 26
        stored_case = await repo.get_case(case.id)
        assert stored_case is not None
        assert len(case.id) == 26
        assert stored_case.input_payload["prompt"] == "hi"

    async def test_audit_repo_create_and_get(self, session: AsyncSession) -> None:
        repo = AuditRepo(session)
        event = await repo.create(event_type="tool_call", status="ok", payload={"tool": "read_file"})
        assert len(event.id) == 26
        fetched = await repo.get_event(event.id)
        assert fetched is not None
        assert fetched.id == event.id
        assert fetched.payload["tool"] == "read_file"

    async def test_checkpoint_repo_create_and_list(self, session: AsyncSession) -> None:
        repo = CheckpointRepo(session)
        entry = await repo.create(
            name="before-upgrade",
            kind="manual",
            target="agent-config",
            payload={"provider": "mock"},
        )
        listed = await repo.list_entries()
        assert listed[0].id == entry.id
        assert listed[0].target == "agent-config"

    async def test_skill_repo_create_and_list(self, session: AsyncSession) -> None:
        repo = SkillRepo(session)
        entry = await repo.create(name="reply-in-chinese", content="Always answer in Chinese.")
        listed = await repo.list_entries()
        assert listed[0].id == entry.id
        assert listed[0].enabled is True

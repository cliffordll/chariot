from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.chat_request import ChatRequest, Message
from chariot.repos.audit_repo import AuditRepo
from chariot.repos.checkpoint_repo import CheckpointRepo
from chariot.database.session import dispose_db, init_db
from chariot.repos.eval_repo import EvalRepo
from chariot.repos.memory_repo import MemoryRepo
from chariot.repos.prompt_repo import PromptRepo
from chariot.repos.skill_repo import SkillRepo


@pytest_asyncio.fixture
async def session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    db_path = tmp_path / "chariot.db"
    sm = await init_db(db_path)
    async with sm() as session:
        yield session
    await dispose_db()


class TestMigrationV9:
    async def test_platform_tables_exist(self, session: AsyncSession) -> None:
        names = {
            "memories",
            "eval_runs",
            "eval_cases",
            "audit_events",
            "checkpoints",
            "skills",
            "prompt_bundles",
            "prompt_versions",
            "prompt_traces",
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

    async def test_prompt_repo_seed_list_and_trace(self, session: AsyncSession) -> None:
        repo = PromptRepo(session)
        await repo.seed_if_empty()

        bundles = await repo.list_bundles()
        assert bundles[0].name == "default"
        assert bundles[0].version_count == 1

        trace = await repo.record_trace(
            ChatRequest(
                provider_name="mock",
                messages=[Message(role="user", content="hi")],
                system="system prompt",
            ),
            provider_name="mock",
            model="mock-1",
        )
        assert len(trace.id) == 26
        fetched = await repo.get_trace(trace.id)
        assert fetched is not None
        assert fetched.provider_name == "mock"

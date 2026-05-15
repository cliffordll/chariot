from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.chat_request import ChatRequest, Message
from chariot.database.session import CURRENT_SCHEMA_VERSION, dispose_db, init_db
from chariot.models.task import TaskCreate, TaskRunCreate
from chariot.repos.audit_repo import AuditRepo
from chariot.repos.checkpoint_repo import CheckpointRepo
from chariot.repos.eval_repo import EvalRepo
from chariot.repos.memory_repo import MemoryRepo
from chariot.repos.prompt_repo import PromptRepo
from chariot.repos.provider_repo import ProviderRepo
from chariot.repos.skill_repo import SkillRepo
from chariot.repos.task_repo import TaskRepo
from chariot.services.task import TaskService


@pytest_asyncio.fixture
async def session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    db_path = tmp_path / "chariot.db"
    sm = await init_db(db_path)
    async with sm() as session:
        yield session
    await dispose_db()


class TestMigrationV10:
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
            "agent_profiles",
            "tasks",
            "task_runs",
            "scheduled_jobs",
            "job_runs",
            "toolsets",
            "toolset_members",
            "trace_turns",
            "trace_provider_calls",
            "trace_tool_calls",
            "trace_checkpoints",
        }
        rows = (
            (
                await session.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
                )
            )
            .scalars()
            .all()
        )
        assert names.issubset(set(rows))

    async def test_agent_profile_identity_columns_exist(self, session: AsyncSession) -> None:
        def _columns(rows: list[dict[str, object]]) -> set[str]:
            return {str(row["name"]) for row in rows}

        agent_profile_cols = (await session.execute(text("PRAGMA table_info(agent_profiles)"))).mappings().all()
        task_cols = (await session.execute(text("PRAGMA table_info(tasks)"))).mappings().all()
        job_cols = (await session.execute(text("PRAGMA table_info(scheduled_jobs)"))).mappings().all()

        assert "id" in _columns(agent_profile_cols)
        assert "agent_profile_id" in _columns(task_cols)
        assert "agent_profile_id" in _columns(job_cols)

    async def test_toolset_identity_columns_exist(self, session: AsyncSession) -> None:
        def _columns(rows: list[dict[str, object]]) -> set[str]:
            return {str(row["name"]) for row in rows}

        toolset_cols = (await session.execute(text("PRAGMA table_info(toolsets)"))).mappings().all()
        member_cols = (await session.execute(text("PRAGMA table_info(toolset_members)"))).mappings().all()
        agent_profile_cols = (await session.execute(text("PRAGMA table_info(agent_profiles)"))).mappings().all()

        assert "id" in _columns(toolset_cols)
        assert "toolset_id" in _columns(member_cols)
        assert "toolset_id" in _columns(agent_profile_cols)

    async def test_job_identity_columns_exist(self, session: AsyncSession) -> None:
        def _columns(rows: list[dict[str, object]]) -> set[str]:
            return {str(row["name"]) for row in rows}

        job_cols = (await session.execute(text("PRAGMA table_info(scheduled_jobs)"))).mappings().all()
        job_run_cols = (await session.execute(text("PRAGMA table_info(job_runs)"))).mappings().all()

        assert "id" in _columns(job_cols)
        assert "job_id" in _columns(job_run_cols)

    async def test_v1_logs_db_upgrades_via_squashed_migration(self, tmp_path: Path) -> None:
        db_path = tmp_path / "legacy-v1.db"
        init_sql = (Path("chariot/database/migrations/001_init.sql")).read_text(encoding="utf-8")

        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(init_sql)
            conn.execute(
                "INSERT INTO logs (id, model, input_tokens, output_tokens, latency_ms, status, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("log-1", "legacy-mock", 10, 20, 30, "ok", None),
            )
            conn.commit()
        finally:
            conn.close()

        sm = await init_db(db_path)
        async with sm() as upgraded:
            version = (await upgraded.execute(text("PRAGMA user_version"))).scalar_one()
            assert version == CURRENT_SCHEMA_VERSION

            log_row = (
                (
                    await upgraded.execute(
                        text("SELECT id, provider, input_tokens, output_tokens, latency_ms, status FROM logs"),
                    )
                )
                .mappings()
                .one()
            )
            assert log_row["id"] == "log-1"
            assert log_row["provider"] == "legacy-mock"
            assert log_row["input_tokens"] == 10
            assert log_row["output_tokens"] == 20
            assert log_row["latency_ms"] == 30
            assert log_row["status"] == "ok"

            provider_row = (
                (await upgraded.execute(text("SELECT id, slug, name FROM providers ORDER BY created_at, id LIMIT 1")))
                .mappings()
                .one()
            )
            assert provider_row["id"] == "provider_mock"
            assert provider_row["slug"] == "mock"
            assert provider_row["name"] == "Mock"

            default_provider_id = (
                await upgraded.execute(text("SELECT default_provider_id FROM settings WHERE id = 1"))
            ).scalar_one()
            assert default_provider_id == "provider_mock"
        await dispose_db()

    async def test_v28_db_auto_bumps_to_current_schema_without_replaying_squashed_sql(self, tmp_path: Path) -> None:
        db_path = tmp_path / "legacy-v28.db"
        sm = await init_db(db_path)
        async with sm() as seeded:
            version = (await seeded.execute(text("PRAGMA user_version"))).scalar_one()
            assert version == CURRENT_SCHEMA_VERSION
        await dispose_db()

        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA user_version = 28")
            conn.commit()
        finally:
            conn.close()

        sm = await init_db(db_path)
        async with sm() as upgraded:
            version = (await upgraded.execute(text("PRAGMA user_version"))).scalar_one()
            assert version == CURRENT_SCHEMA_VERSION
            default_provider_id = (
                await upgraded.execute(text("SELECT default_provider_id FROM settings WHERE id = 1"))
            ).scalar_one()
            assert default_provider_id is not None
        await dispose_db()


class TestPlatformRepos:
    async def test_provider_repo_default_slug_uses_type_and_name(self, session: AsyncSession) -> None:
        repo = ProviderRepo(session)
        created = await repo.create(name="Qwen 3 32B", type="mock", options={})
        assert created.slug == "mock-qwen-3-32b"

    async def test_provider_repo_default_slug_auto_suffixes_conflicts(self, session: AsyncSession) -> None:
        repo = ProviderRepo(session)
        first = await repo.create(name="Qwen", type="mock", options={})
        second = await repo.create(name="Qwen", type="mock", options={})
        assert first.slug == "mock-qwen"
        assert second.slug == "mock-qwen-2"

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
        assert bundles[0].is_active is True
        assert bundles[0].active_version == "v1"

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
        assert fetched.provider_name_snapshot == "mock"

        active_bundle = await repo.get_active_bundle()
        assert active_bundle is not None
        assert active_bundle.name == "default"

    async def test_task_repo_create_profile_task_run_and_job(self, session: AsyncSession) -> None:
        repo = TaskRepo(session)
        mock = await ProviderRepo(session).get_entry("mock")
        from chariot.repos.toolset_repo import ToolsetRepo

        toolset = await ToolsetRepo(session).create(name="default", members=["read_file"])
        profile = await repo.create_agent_profile(
            name="planner",
            role="planner",
            tool_profile="default",
            provider_id="mock",
            budget={"max_steps": 3},
        )
        assert mock is not None
        assert len(toolset.id) == 26
        assert len(profile.id) == 26
        assert profile.toolset_id == toolset.id
        assert profile.provider_id == mock.id
        task = await repo.create_task(
            TaskCreate(
                goal="split work into child tasks",
                agent_profile=profile.name,
                owner="user",
                meta={"source": "test"},
            )
        )
        run = await repo.create_run(TaskRunCreate(task_id=task.id, trigger="manual"))
        job = await repo.create_job(name="cleanup", goal="cleanup stale state", cron="0 * * * *")
        job_run = await repo.create_job_run(job_name=job.name, task_id=task.id, status="queued")

        assert (await repo.get_agent_profile(profile.name)) is not None
        by_id = await repo.get_agent_profile(profile.id)
        assert by_id is not None
        assert by_id.name == profile.name
        assert by_id.toolset_id == toolset.id
        stored_task = await repo.get_task(task.id)
        assert stored_task is not None
        assert stored_task.agent_profile == "planner"
        assert stored_task.status.value == "queued"
        task_row = (
            (
                await session.execute(
                    text("SELECT agent_profile, agent_profile_id FROM tasks WHERE id = :id"), {"id": task.id}
                )
            )
            .mappings()
            .one()
        )
        assert task_row["agent_profile"] == "planner"
        assert task_row["agent_profile_id"] == profile.id
        runs = await repo.list_runs(task.id)
        assert runs[0].id == run.id
        jobs = await repo.list_jobs()
        assert jobs[0].name == job.name
        assert len(jobs[0].id) == 26
        updated_job = await repo.update_job(name=job.name, agent_profile=profile.id)
        assert updated_job.agent_profile == "planner"
        job_row = (
            (
                await session.execute(
                    text("SELECT id, agent_profile, agent_profile_id FROM scheduled_jobs WHERE name = :name"),
                    {"name": job.name},
                )
            )
            .mappings()
            .one()
        )
        assert len(str(job_row["id"])) == 26
        assert job_row["agent_profile"] == "planner"
        assert job_row["agent_profile_id"] == profile.id
        job_runs = await repo.list_job_runs(job.name)
        assert job_runs[0].id == job_run.id
        assert job_runs[0].job_id == jobs[0].id
        assert (await repo.list_job_runs(jobs[0].id))[0].id == job_run.id

    async def test_task_repo_update_delete_agent_and_delete_job(self, session: AsyncSession) -> None:
        repo = TaskRepo(session)
        await repo.create_agent_profile(name="planner", role="planner")
        updated = await repo.update_agent_profile(
            name="planner",
            role="executor",
            tool_profile="default",
            meta={"scope": "repo"},
        )
        assert updated.role == "executor"
        assert updated.tool_profile == "default"
        assert updated.meta["scope"] == "repo"
        await repo.delete_agent_profile("planner")
        assert await repo.get_agent_profile("planner") is None

        await repo.create_job(name="cleanup", goal="cleanup stale state", cron="0 * * * *")
        await repo.delete_job("cleanup")
        assert await repo.get_job("cleanup") is None

    async def test_task_repo_clear_agent_binding_fields(self, session: AsyncSession) -> None:
        """显式传 None 应清空 binding;未传(UNSET 默认)保持原值。"""
        repo = TaskRepo(session)
        from chariot.repos.toolset_repo import ToolsetRepo

        toolset = await ToolsetRepo(session).create(name="fs_safe")
        await repo.create_agent_profile(
            name="a1",
            role="r",
            prompt_bundle="research",
            tool_profile="fs_safe",
            provider_id="claude",
        )
        # 1) 未传 prompt/tool/provider → 都保留;只改 role
        u1 = await repo.update_agent_profile(name="a1", role="executor")
        assert u1.prompt_bundle == "research"
        assert u1.tool_profile == "fs_safe"
        assert u1.toolset_id == toolset.id
        assert u1.provider_id == "claude"
        # 2) 显式 None → 清空 provider,其它仍保留
        u2 = await repo.update_agent_profile(name="a1", provider_id=None)
        assert u2.provider_id is None
        assert u2.prompt_bundle == "research"
        assert u2.tool_profile == "fs_safe"
        # 3) 同时清两个
        u3 = await repo.update_agent_profile(name="a1", prompt_bundle=None, tool_profile=None)
        assert u3.prompt_bundle is None
        assert u3.tool_profile is None
        assert u3.toolset_id is None
        # 4) 重新 set
        u4 = await repo.update_agent_profile(name="a1", provider_id="ollama")
        assert u4.provider_id == "ollama"

    async def test_task_repo_update_and_toggle_job(self, session: AsyncSession) -> None:
        repo = TaskRepo(session)
        await repo.create_job(name="cleanup", goal="cleanup stale state", cron="0 * * * *", enabled=False)

        updated = await repo.update_job(
            name="cleanup",
            goal="cleanup tmp files",
            cron="*/5 * * * *",
            meta={"scope": "repo"},
        )
        assert updated.goal == "cleanup tmp files"
        assert updated.cron == "*/5 * * * *"
        assert updated.meta["scope"] == "repo"

        enabled = await repo.set_job_enabled("cleanup", True)
        assert enabled.enabled is True

    async def test_task_service_can_fail_and_cancel_runs(self, session: AsyncSession) -> None:
        repo = TaskRepo(session)
        service = TaskService(repo)
        failed_task = await service.create_task(TaskCreate(goal="worker fail"))
        failed_run = await service.start_task_run(TaskRunCreate(task_id=failed_task.id))
        failed = await service.fail_task_run(failed_run.id, error="boom")
        assert failed.status.value == "failed"
        assert (await repo.get_task(failed_task.id)).status.value == "failed"

        cancelled_task = await service.create_task(TaskCreate(goal="worker cancel"))
        cancelled_run = await service.start_task_run(TaskRunCreate(task_id=cancelled_task.id))
        cancelled = await service.cancel_task_run(cancelled_run.id, error="user requested")
        assert cancelled.status.value == "cancelled"
        assert (await repo.get_task(cancelled_task.id)).status.value == "cancelled"

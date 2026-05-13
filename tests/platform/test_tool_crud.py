"""ToolRepo CRUD tests for custom tools (0.8.7 Wave 3)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.exceptions import ConfigError, ToolNotFound
from chariot.database.models import ToolRow
from chariot.database.session import dispose_db, init_db
from chariot.repos.tool_repo import ToolRepo
from chariot.agent.run import AIAgent


@pytest_asyncio.fixture
async def session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    db_path = tmp_path / "chariot.db"
    sm = await init_db(db_path)
    async with sm() as s:
        yield s
    await dispose_db()


class TestToolRepoCrud:
    async def test_create_custom_persists_to_db(self, session: AsyncSession) -> None:
        repo = ToolRepo(session)
        tool = await repo.create(
            name="my_api",
            type="http_custom",
            enabled=True,
            options={"url_template": "https://api.example.com/${id}"},
            source="custom",
            description="Test API",
            custom_type="http_custom",
        )
        assert tool.name == "my_api"
        assert tool.custom_type == "http_custom"
        assert tool.source == "custom"
        assert tool.description == "Test API"

        loaded = await repo.get_entry("my_api")
        assert loaded is not None
        assert loaded.name == "my_api"
        assert loaded.custom_type == "http_custom"

    async def test_delete_custom_ok(self, session: AsyncSession) -> None:
        repo = ToolRepo(session)
        await repo.create(
            name="to_delete",
            type="shell_custom",
            enabled=True,
            options={"command_template": "echo hi"},
            source="custom",
            custom_type="shell_custom",
        )
        await repo.delete("to_delete")
        assert await repo.get_entry("to_delete") is None

    async def test_delete_builtin_rejected(self, session: AsyncSession) -> None:
        repo = ToolRepo(session)
        await repo.seed_if_empty()
        entries = await repo.list_entries()
        builtin = next(e for e in entries if e.source == "builtin")
        with pytest.raises(ConfigError, match=r"builtin.*不可删除"):
            await repo.delete(builtin.name)

    async def test_update_custom_updates_options(self, session: AsyncSession) -> None:
        repo = ToolRepo(session)
        await repo.create(
            name="updatable",
            type="http_custom",
            enabled=True,
            options={"url_template": "https://api.example.com/${id}", "timeout_s": 10},
            source="custom",
            custom_type="http_custom",
        )
        updated = await repo.update_full(
            "updatable",
            options={"timeout_s": 30, "max_bytes": 2048},
            description="Updated desc",
        )
        assert updated.options["timeout_s"] == 30
        assert updated.options["max_bytes"] == 2048
        assert updated.description == "Updated desc"

    async def test_create_duplicate_name_raises(self, session: AsyncSession) -> None:
        repo = ToolRepo(session)
        await repo.create(
            name="dup",
            type="http_custom",
            enabled=True,
            options={"url_template": "https://api.example.com/${id}"},
            source="custom",
            custom_type="http_custom",
        )
        with pytest.raises(ConfigError, match="已存在"):
            await repo.create(
                name="dup",
                type="http_custom",
                enabled=True,
                options={"url_template": "https://api.example.com/${id}"},
                source="custom",
                custom_type="http_custom",
            )

    async def test_update_missing_raises(self, session: AsyncSession) -> None:
        repo = ToolRepo(session)
        with pytest.raises(ToolNotFound):
            await repo.update_full("nope", options={})

    async def test_delete_missing_raises(self, session: AsyncSession) -> None:
        repo = ToolRepo(session)
        with pytest.raises(ToolNotFound):
            await repo.delete("nope")

    async def test_create_invalid_custom_rejected_before_persist(self, session: AsyncSession) -> None:
        repo = ToolRepo(session)
        with pytest.raises(ConfigError, match="command_template"):
            await repo.create(
                name="bad_shell",
                type="shell_custom",
                enabled=True,
                options={"workdir": "."},
                source="custom",
                custom_type="shell_custom",
            )
        assert await repo.get_entry("bad_shell") is None

    async def test_update_invalid_custom_rejected_before_persist(self, session: AsyncSession) -> None:
        repo = ToolRepo(session)
        await repo.create(
            name="editable_shell",
            type="shell_custom",
            enabled=True,
            options={"command_template": "echo hi"},
            source="custom",
            custom_type="shell_custom",
        )
        with pytest.raises(ConfigError, match="workdir"):
            await repo.update_full(
                "editable_shell",
                options={"workdir": 123},
            )
        loaded = await repo.get_entry("editable_shell")
        assert loaded is not None
        assert loaded.options["command_template"] == "echo hi"

    async def test_bootstrap_skips_invalid_custom_tool(self, tmp_path: Path) -> None:
        db_path = tmp_path / "bootstrap.db"
        sm = await init_db(db_path)
        try:
            async with sm() as session:
                repo = ToolRepo(session)
                await repo.seed_if_empty()
                session.add(
                    ToolRow(
                        name="broken_shell",
                        type="shell_custom",
                        enabled=1,
                        options='{"timeout_s": 10}',
                        source="custom",
                        custom_type="shell_custom",
                    )
                )
                await session.commit()
            agent = await AIAgent.bootstrap(db_path)
            assert "broken_shell" not in agent.tools
        finally:
            await dispose_db()

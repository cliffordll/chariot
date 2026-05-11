"""ToolsetRepo / ToolsetService unit tests(v16)。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.exceptions import ConfigError
from chariot.database.session import dispose_db, init_db
from chariot.repos.toolset_repo import ToolsetRepo
from chariot.services.toolset import ToolsetService


@pytest_asyncio.fixture
async def session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    db_path = tmp_path / "chariot.db"
    sm = await init_db(db_path)
    async with sm() as s:
        yield s
    await dispose_db()


class TestToolsetRepo:
    async def test_create_get_delete(self, session: AsyncSession) -> None:
        repo = ToolsetRepo(session)
        toolset = await repo.create(
            name="fs_safe",
            description="只读文件操作",
            members=["read_file", "list_dir"],
        )
        assert toolset.name == "fs_safe"
        assert toolset.description == "只读文件操作"
        assert toolset.members == ("list_dir", "read_file")  # 排序后的

        loaded = await repo.get_entry("fs_safe")
        assert loaded is not None
        assert loaded.members == ("list_dir", "read_file")

        await repo.delete("fs_safe")
        assert await repo.get_entry("fs_safe") is None

    async def test_duplicate_name_raises(self, session: AsyncSession) -> None:
        repo = ToolsetRepo(session)
        await repo.create(name="dup")
        with pytest.raises(ConfigError):
            await repo.create(name="dup")

    async def test_empty_name_raises(self, session: AsyncSession) -> None:
        repo = ToolsetRepo(session)
        with pytest.raises(ConfigError):
            await repo.create(name="")

    async def test_update_members(self, session: AsyncSession) -> None:
        repo = ToolsetRepo(session)
        await repo.create(name="ts", members=["read_file"])
        updated = await repo.update("ts", members=["list_dir", "http_get"])
        assert updated.members == ("http_get", "list_dir")

    async def test_update_missing_raises(self, session: AsyncSession) -> None:
        repo = ToolsetRepo(session)
        with pytest.raises(ConfigError):
            await repo.update("nope", description="x")

    async def test_add_remove_member(self, session: AsyncSession) -> None:
        repo = ToolsetRepo(session)
        await repo.create(name="ts")
        await repo.add_member("ts", "read_file")
        await repo.add_member("ts", "read_file")  # 幂等
        await repo.add_member("ts", "list_dir")
        loaded = await repo.get_entry("ts")
        assert loaded is not None
        assert loaded.members == ("list_dir", "read_file")

        await repo.remove_member("ts", "read_file")
        loaded = await repo.get_entry("ts")
        assert loaded is not None
        assert loaded.members == ("list_dir",)

    async def test_delete_cascades_members(self, session: AsyncSession) -> None:
        repo = ToolsetRepo(session)
        await repo.create(name="ts", members=["read_file", "list_dir"])
        await repo.delete("ts")
        # 重建同名 toolset,成员应是空
        again = await repo.create(name="ts")
        assert again.members == ()

    async def test_list_entries_ordered(self, session: AsyncSession) -> None:
        repo = ToolsetRepo(session)
        await repo.create(name="first")
        await repo.create(name="second", members=["read_file"])
        entries = await repo.list_entries()
        names = [e.name for e in entries]
        assert names == ["first", "second"]
        assert entries[1].members == ("read_file",)


class TestToolsetService:
    async def test_service_wraps_repo(self, session: AsyncSession) -> None:
        service = ToolsetService(ToolsetRepo(session))
        toolset = await service.create(name="ts", members=["read_file"])
        assert toolset.members == ("read_file",)

        listed = await service.list_entries()
        assert len(listed) == 1

        await service.add_member("ts", "list_dir")
        loaded = await service.get_entry("ts")
        assert loaded is not None
        assert loaded.members == ("list_dir", "read_file")

        await service.delete("ts")
        assert await service.get_entry("ts") is None

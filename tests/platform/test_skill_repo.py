"""B6 wave 1 step 1b —— `SkillRepo` CRUD 扩展(update/delete/set_enabled)。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.config import ConfigError
from chariot.database.session import dispose_db, init_db
from chariot.repos.skill_repo import SkillRepo


@pytest_asyncio.fixture
async def session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    sm = await init_db(tmp_path / "skills.db")
    async with sm() as session:
        yield session
    await dispose_db()


# ---- get_by_name ----


async def test_get_by_name_returns_none_when_missing(session: AsyncSession) -> None:
    repo = SkillRepo(session)
    assert await repo.get_by_name("nonexistent") is None


async def test_get_by_name_finds_existing(session: AsyncSession) -> None:
    repo = SkillRepo(session)
    created = await repo.create(name="x", content="schema_version: 1\nname: x\ndescription: y\nprompt: z\n")
    found = await repo.get_by_name("x")
    assert found is not None
    assert found.id == created.id


# ---- update ----


async def test_update_changes_description_and_content(session: AsyncSession) -> None:
    repo = SkillRepo(session)
    entry = await repo.create(name="x", description="old", content="old yaml")
    updated = await repo.update(entry.id, description="new", content="new yaml")
    assert updated.description == "new"
    assert updated.content == "new yaml"
    # name 不可变
    assert updated.name == "x"


async def test_update_missing_raises(session: AsyncSession) -> None:
    repo = SkillRepo(session)
    with pytest.raises(ConfigError, match="not found"):
        await repo.update("does-not-exist", description="x")


async def test_update_meta_serialized(session: AsyncSession) -> None:
    repo = SkillRepo(session)
    entry = await repo.create(name="x", content="yaml")
    updated = await repo.update(entry.id, meta={"foo": "bar", "n": 1})
    assert updated.meta == {"foo": "bar", "n": 1}


# ---- set_enabled ----


async def test_set_enabled_toggles(session: AsyncSession) -> None:
    repo = SkillRepo(session)
    entry = await repo.create(name="x", content="yaml", enabled=True)
    disabled = await repo.set_enabled(entry.id, False)
    assert disabled.enabled is False
    enabled = await repo.set_enabled(entry.id, True)
    assert enabled.enabled is True


async def test_set_enabled_missing_raises(session: AsyncSession) -> None:
    repo = SkillRepo(session)
    with pytest.raises(ConfigError):
        await repo.set_enabled("nope", True)


# ---- delete ----


async def test_delete_existing_returns_true(session: AsyncSession) -> None:
    repo = SkillRepo(session)
    entry = await repo.create(name="x", content="yaml")
    assert await repo.delete(entry.id) is True
    assert await repo.get_entry(entry.id) is None


async def test_delete_missing_returns_false(session: AsyncSession) -> None:
    repo = SkillRepo(session)
    assert await repo.delete("does-not-exist") is False


# ---- duplicate name ----


async def test_create_duplicate_name_raises(session: AsyncSession) -> None:
    repo = SkillRepo(session)
    await repo.create(name="dup", content="x")
    with pytest.raises(ConfigError, match="已存在"):
        await repo.create(name="dup", content="y")

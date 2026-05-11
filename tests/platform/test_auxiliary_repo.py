"""B3 wave 2 — `AuxiliaryRepo` CRUD + UNSET sentinel 清空语义。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.exceptions import (
    AuxiliaryClientNotFound,
    DuplicateAuxiliaryClientName,
)
from chariot.database.session import dispose_db, init_db
from chariot.models.agent import UNSET
from chariot.repos.auxiliary_repo import AuxiliaryRepo


@pytest_asyncio.fixture
async def session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    sm = await init_db(tmp_path / "test.db")
    async with sm() as s:
        yield s
    await dispose_db()


async def test_seed_summarizer_present(session: AsyncSession) -> None:
    """v19 migration seed 一条 'summarizer' 指向 mock,开箱即用。"""
    entry = await AuxiliaryRepo(session).get_entry("summarizer")
    assert entry is not None
    assert entry.provider_entry == "mock"
    assert entry.params.get("max_tokens") == 512


async def test_create_and_get(session: AsyncSession) -> None:
    repo = AuxiliaryRepo(session)
    entry = await repo.create(
        name="critic_aux",
        provider_entry="mock",
        model="mock-critic",
        params={"max_tokens": 1024, "temperature": 0.2},
    )
    assert entry.name == "critic_aux"
    assert entry.model == "mock-critic"
    fetched = await repo.get_entry("critic_aux")
    assert fetched == entry


async def test_create_duplicate_raises(session: AsyncSession) -> None:
    """seed 已有 'summarizer';再 create 同名应 raise。"""
    with pytest.raises(DuplicateAuxiliaryClientName):
        await AuxiliaryRepo(session).create(name="summarizer", provider_entry="mock")


async def test_update_provider_entry(session: AsyncSession) -> None:
    repo = AuxiliaryRepo(session)
    await repo.create(name="aux1", provider_entry="mock", model="mock-1")
    entry = await repo.update("aux1", provider_entry="other")
    assert entry.provider_entry == "other"
    # model 不传 → 不动
    assert entry.model == "mock-1"


async def test_update_model_clear_with_none(session: AsyncSession) -> None:
    """model=None 显式 clear(UNSET 区分"不传");repo `update(model=None)`。"""
    repo = AuxiliaryRepo(session)
    await repo.create(name="aux1", provider_entry="mock", model="mock-1")
    entry = await repo.update("aux1", model=None)
    assert entry.model is None


async def test_update_model_unset_keeps_value(session: AsyncSession) -> None:
    repo = AuxiliaryRepo(session)
    await repo.create(name="aux1", provider_entry="mock", model="mock-1")
    entry = await repo.update("aux1", model=UNSET, params={"x": 1})
    assert entry.model == "mock-1"  # 未动
    assert entry.params == {"x": 1}  # 替换


async def test_update_not_found_raises(session: AsyncSession) -> None:
    with pytest.raises(AuxiliaryClientNotFound):
        await AuxiliaryRepo(session).update("ghost", provider_entry="mock")


async def test_delete(session: AsyncSession) -> None:
    repo = AuxiliaryRepo(session)
    await repo.create(name="aux1", provider_entry="mock")
    await repo.delete("aux1")
    assert await repo.get_entry("aux1") is None


async def test_delete_not_found_raises(session: AsyncSession) -> None:
    with pytest.raises(AuxiliaryClientNotFound):
        await AuxiliaryRepo(session).delete("ghost")


async def test_list_includes_created_order(session: AsyncSession) -> None:
    repo = AuxiliaryRepo(session)
    await repo.create(name="aaa", provider_entry="mock")
    await repo.create(name="bbb", provider_entry="mock")
    entries = await repo.list_entries()
    names = [e.name for e in entries]
    # seed summarizer 先;然后 aaa, bbb
    assert "summarizer" in names
    assert names.index("aaa") < names.index("bbb")

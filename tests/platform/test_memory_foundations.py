from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.database.session import dispose_db, init_db
from chariot.repos.memory_repo import MemoryRepo


@pytest_asyncio.fixture
async def session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    db_path = tmp_path / "chariot.db"
    sm = await init_db(db_path)
    async with sm() as session:
        yield session
    await dispose_db()


@pytest.mark.asyncio
async def test_memory_migration_v13_tables_exist(session: AsyncSession) -> None:
    names = {"memories", "memory_events", "memory_links"}
    rows = (
        await session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        )
    ).scalars().all()
    assert names.issubset(set(rows))


@pytest.mark.asyncio
async def test_memory_repo_create_update_pin_archive_and_links(session: AsyncSession) -> None:
    repo = MemoryRepo(session)
    entry = await repo.create(
        kind="preference",
        text="Reply in Chinese.",
        meta={"scope": "user"},
        links=[
            {"link_type": "conversation", "link_value": "01H_MEMORY_CONVO"},
            {"link_type": "provider", "link_value": "mock"},
        ],
    )
    assert len(entry.id) == 26

    updated = await repo.update(entry.id, pinned=True, text="Reply in Chinese and keep it concise.")
    archived = await repo.archive(entry.id)
    events = await repo.list_events(memory_id=entry.id)
    links = await repo.list_links(memory_id=entry.id)
    search = await repo.search_entries("concise")

    assert updated.pinned is True
    assert archived.archived is True
    assert [event.event_type for event in events][:3] == ["archived", "updated", "created"]
    assert {link.link_type for link in links} == {"conversation", "provider"}
    assert search[0].id == entry.id

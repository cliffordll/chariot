from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.chat_request import ChatRequest, Message
from chariot.database.session import dispose_db, init_db
from chariot.memory.capture import MemoryCaptureService
from chariot.memory.policy import MemoryPolicy
from chariot.repos.memory_repo import MemoryRepo


@pytest_asyncio.fixture
async def session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    db_path = tmp_path / "chariot.db"
    sm = await init_db(db_path)
    async with sm() as session:
        yield session
    await dispose_db()


@pytest.mark.asyncio
async def test_memory_capture_extracts_preference_candidate(session: AsyncSession) -> None:
    repo = MemoryRepo(session)
    capture = MemoryCaptureService(repo)
    req = ChatRequest(
        provider_name="mock",
        messages=[Message(role="user", content="默认用中文输出但保留关键 English terms")],
    )

    candidates = capture.extract_from_request(req, provider_name="mock")
    assert len(candidates) == 1
    assert candidates[0].kind == "preference"
    assert candidates[0].text == "默认用中文输出但保留关键 English terms"


@pytest.mark.asyncio
async def test_memory_capture_turn_persists_memory(session: AsyncSession) -> None:
    repo = MemoryRepo(session)
    capture = MemoryCaptureService(repo)
    req = ChatRequest(
        provider_name="mock",
        messages=[Message(role="user", content="默认用中文输出但保留关键 English terms")],
    )

    await capture.capture_turn(req=req, provider_name="mock", policy=MemoryPolicy())
    entries = await repo.list_entries(kind="preference")

    assert len(entries) == 1
    assert entries[0].text == "默认用中文输出但保留关键 English terms"

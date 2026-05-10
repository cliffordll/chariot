from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from chariot.agent.chat_request import ChatRequest, Message
from chariot.agent.registry import AgentRegistry
from chariot.agent.run import AIAgent
from chariot.database.session import dispose_db
from chariot.repos.memory_repo import MemoryRepo


@pytest_asyncio.fixture(autouse=True)
async def _isolate() -> AsyncIterator[None]:
    await AgentRegistry.clear()
    await dispose_db()
    yield
    await AgentRegistry.clear()
    await dispose_db()


@pytest_asyncio.fixture
async def agent(tmp_path: Path) -> AIAgent:
    return await AIAgent.bootstrap(tmp_path / "chariot.db")


@pytest.mark.asyncio
async def test_memory_is_auto_captured_after_turn(agent: AIAgent) -> None:
    req = ChatRequest(
        provider_name="mock",
        messages=[Message(role="user", content="默认用中文输出但保留关键 English terms")],
    )

    async for _event in agent.run_chat(req):
        pass

    async with agent.session_maker() as session:
        entries = await MemoryRepo(session).list_entries(kind="preference")

    assert any(
        entry.text == "默认用中文输出但保留关键 English terms"
        for entry in entries
    )

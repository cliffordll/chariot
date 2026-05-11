from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest_asyncio

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest, Message
from chariot.agent.run import AIAgent
from chariot.database.session import dispose_db, init_db
from chariot.providers.base import BaseProvider, BaseProviderConfig
from chariot.repos.prompt_repo import PromptRepo


class _PromptProvider(BaseProvider):
    def __init__(self) -> None:
        self.config = BaseProviderConfig(name="mock", model="mock-1")

    @classmethod
    def create(cls, options: dict[str, Any]) -> _PromptProvider:
        return cls()

    async def generate(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        yield ChatEvent.message_start(message_id="m1", model=self.config.model)
        yield ChatEvent.text_block_start(index=0)
        yield ChatEvent.text_delta("ok", index=0)
        yield ChatEvent.block_stop(index=0)
        yield ChatEvent.message_delta_done(stop_reason="end_turn")
        yield ChatEvent.message_done()


@pytest_asyncio.fixture
async def sessionmaker(tmp_path: Path):
    db_path = tmp_path / "chariot.db"
    sm = await init_db(db_path)
    try:
        yield sm
    finally:
        await dispose_db()


class TestPromptSystem:
    async def test_stateless_chat_records_prompt_trace(self, sessionmaker) -> None:
        agent = AIAgent(providers={"mock": _PromptProvider()}, tools={}, sessionmaker=sessionmaker)
        req = ChatRequest(
            provider_name="mock",
            messages=[Message(role="user", content="hi")],
            system="system prompt",
        )

        events = [event async for event in agent.run_chat(req)]
        assert any(event.kind == "stream_done" for event in events)

        async with sessionmaker() as session:
            traces = await PromptRepo(session).list_traces()
        assert len(traces) == 1
        trace = traces[0]
        assert trace.provider_name == "mock"
        assert trace.request["system"] == "system prompt"
        assert trace.source_refs[0]["layer"] == "base_system"

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from chariot.agent.chat_request import ChatRequest, Message
from chariot.agent.registry import AgentRegistry
from chariot.agent.run import AIAgent
from chariot.database.session import dispose_db
from chariot.repos.context_repo import ContextRepo
from chariot.repos.memory_repo import MemoryRepo
from chariot.repos.prompt_repo import PromptRepo


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
async def test_memory_is_injected_into_context_and_prompt(agent: AIAgent) -> None:
    conversation_id = "01H00000000000000000000000"
    req = ChatRequest(
        provider_name="mock",
        messages=[Message(role="user", content="hello")],
        conversation_id=conversation_id,
    )

    async with agent.session_maker() as session:
        await MemoryRepo(session).create(
            kind="preference",
            text="Reply in Chinese.",
            meta={"scope": "user"},
            links=[{"link_type": "conversation", "link_value": conversation_id}],
        )

    async for _event in agent.run_chat(req):
        pass

    async with agent.session_maker() as session:
        context_repo = ContextRepo(session)
        snapshots = await context_repo.list_snapshots(conversation_id=conversation_id)
        traces = await context_repo.list_traces(conversation_id=conversation_id)
        prompt_trace = await PromptRepo(session).get_trace(traces[0].prompt_trace_id)

    assert snapshots[0].slices[2]["content"][0]["text"] == "Reply in Chinese."
    assert prompt_trace is not None
    assert any(
        ref["layer"] == "memory" and ref["present"] is True
        for ref in prompt_trace.source_refs
    )

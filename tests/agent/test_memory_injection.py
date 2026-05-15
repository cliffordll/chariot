from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from chariot.agent.chat_request import ChatRequest, Message
from chariot.agent.registry import AgentRegistry
from chariot.agent.run import AIAgent
from chariot.database.session import dispose_db
from chariot.services.context import ContextService
from chariot.services.memory import MemoryService
from chariot.services.prompt import PromptService


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

    await MemoryService(agent).create(
        kind="preference",
        text="Reply in Chinese.",
        meta={"scope": "user"},
        links=[{"link_type": "conversation", "link_value": conversation_id}],
    )

    async for _event in agent.run_chat(req):
        pass

    context_service = ContextService(agent)
    snapshots = await context_service.list_snapshots(conversation_id=conversation_id)
    traces = await context_service.list_traces(conversation_id=conversation_id)
    prompt_trace = await PromptService(agent).get_trace(traces[0].prompt_trace_id)

    memory_state = snapshots[0].slices[2]["content"]
    assert memory_state["entries"][0]["text"] == "Reply in Chinese."
    assert memory_state["policy"]["name"] == "default_memory_policy"
    assert prompt_trace is not None
    assert any(ref["layer"] == "memory" and ref["present"] is True for ref in prompt_trace.source_refs)

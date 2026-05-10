"""Context foundation tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from chariot.agent.chat_request import ChatRequest, Message
from chariot.agent.registry import AgentRegistry
from chariot.agent.run import AIAgent
from chariot.context.composer import build_snapshot
from chariot.database.session import dispose_db
from chariot.repos.context_repo import ContextRepo


@pytest_asyncio.fixture(autouse=True)
async def _isolate() -> AsyncIterator[None]:
    await AgentRegistry.clear()
    await dispose_db()
    yield
    await AgentRegistry.clear()
    await dispose_db()


@pytest_asyncio.fixture
async def agent(tmp_path: Path) -> AIAgent:
    db_path = tmp_path / "chariot.db"
    return await AIAgent.bootstrap(db_path)


def _stateful_req(conversation_id: str) -> ChatRequest:
    return ChatRequest(
        provider_name="mock",
        messages=[Message(role="user", content="hello")],
        conversation_id=conversation_id,
    )


@pytest.mark.asyncio
async def test_context_repo_records_snapshot_and_trace(agent: AIAgent) -> None:
    conversation_id = "01H00000000000000000000000"
    req = _stateful_req(conversation_id)

    async with agent.session_maker() as session:
        repo = ContextRepo(session)
        snapshot = build_snapshot(
            req,
            provider_name="mock",
            model="mock-1",
            history=[{"role": "user", "content": "hello"}],
            provider_capabilities={"supports_system": False},
        )
        recorded = await repo.record_snapshot(snapshot)
        trace = await repo.record_trace(recorded.id, prompt_trace_id="prompt_trace_01")

        listed_snapshots = await repo.list_snapshots()
        listed_traces = await repo.list_traces()
        inspected = await repo.inspect_context(recorded.id)

    assert len(listed_snapshots) == 1
    assert len(listed_traces) == 1
    assert recorded.id == listed_snapshots[0].id
    assert trace.snapshot_id == recorded.id
    assert inspected is not None
    assert inspected["snapshot"]["id"] == recorded.id
    assert inspected["trace"]["snapshot_id"] == recorded.id


@pytest.mark.asyncio
async def test_stateful_chat_writes_context_records(agent: AIAgent) -> None:
    conversation_id = "01H00000000000000000000000"
    req = _stateful_req(conversation_id)

    async for _event in agent.run_chat(req):
        pass

    async with agent.session_maker() as session:
        repo = ContextRepo(session)
        snapshots = await repo.list_snapshots()
        traces = await repo.list_traces()

    assert len(snapshots) == 1
    assert len(traces) == 1
    snapshot = snapshots[0]
    trace = traces[0]
    assert snapshot.conversation_id == conversation_id
    assert [slice_["name"] for slice_ in snapshot.slices] == [
        "conversation_history",
        "runtime_state",
        "memory_state",
        "tool_state",
        "skill_state",
        "provider_state",
        "policy_state",
    ]
    assert trace.snapshot_id == snapshot.id
    assert trace.prompt_trace_id is not None
    assert trace.selected_refs[0]["included"] is True

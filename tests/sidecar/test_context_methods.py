"""Sidecar context method tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from chariot.agent.chat_request import ChatRequest, Message
from chariot.agent.registry import AgentRegistry
from chariot.agent.run import AIAgent
from chariot.context.composer import build_snapshot
from chariot.database.session import dispose_db
from chariot.repos.context_repo import ContextRepo
from chariot.rpc.jsonrpc import JsonRpcServer
from chariot.sidecar.methods import register_methods


def _make_reader(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


class _Writer:
    def __init__(self) -> None:
        self.buf = bytearray()

    def write(self, data: bytes) -> None:
        self.buf.extend(data)

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def lines(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.buf.split(b"\n") if line.strip()]


async def _call(server: JsonRpcServer, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    reader = _make_reader((json.dumps(body) + "\n").encode())
    writer = _Writer()
    await server.serve(reader, writer)
    return writer.lines()[0]


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


@pytest.fixture
def server(agent: AIAgent, tmp_path: Path) -> JsonRpcServer:
    server = JsonRpcServer()
    register_methods(server, agent, db_path=tmp_path / "chariot.db")
    return server


@pytest.mark.asyncio
async def test_list_and_inspect_context_methods(server: JsonRpcServer, agent: AIAgent) -> None:
    conversation_id = "01H00000000000000000000000"
    req = ChatRequest(
        provider_name="mock",
        messages=[Message(role="user", content="hello")],
        conversation_id=conversation_id,
    )

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
        await repo.record_trace(recorded.id, prompt_trace_id="prompt_trace_01")

    listed = await _call(server, "list_context_snapshots")
    assert listed["result"]["snapshots"][0]["id"] == recorded.id

    inspected = await _call(server, "inspect_context", {"context_id": recorded.id})
    assert inspected["result"]["snapshot"]["id"] == recorded.id
    assert inspected["result"]["trace"]["snapshot_id"] == recorded.id

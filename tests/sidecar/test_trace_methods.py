"""sidecar trace RPC dispatch tests。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from chariot.agent.registry import AgentRegistry
from chariot.agent.run import AIAgent
from chariot.database.session import dispose_db
from chariot.models.trace import ToolCallStatus, TurnStatus
from chariot.repos.trace_repo import TraceRepo
from chariot.rpc.jsonrpc import JsonRpcServer
from chariot.sidecar.methods import register_methods


def make_reader(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


class MockWriter:
    def __init__(self) -> None:
        self.buf = bytearray()

    def write(self, data: bytes) -> None:
        self.buf.extend(data)

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def lines(self) -> list[dict[str, Any]]:
        return [json.loads(s) for s in self.buf.split(b"\n") if s.strip()]


def _request_frame(rid: int, method: str, params: dict[str, Any] | None = None) -> bytes:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        body["params"] = params
    return (json.dumps(body) + "\n").encode()


async def _call(server: JsonRpcServer, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    reader = make_reader(_request_frame(1, method, params))
    writer = MockWriter()
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
    db_path = tmp_path / "chariot.db"
    return await AIAgent.bootstrap(db_path)


@pytest.fixture
def server(agent: AIAgent, tmp_path: Path) -> JsonRpcServer:
    s = JsonRpcServer()
    register_methods(s, agent, db_path=tmp_path / "chariot.db")
    return s


class TestTraceMethods:
    async def test_list_empty(self, server: JsonRpcServer) -> None:
        line = await _call(server, "list_traces")
        assert line["result"] == {"turns": []}

    async def test_list_show_view(self, server: JsonRpcServer, agent: AIAgent) -> None:
        async with agent.session_maker() as session:
            repo = TraceRepo(session)
            turn = await repo.create_turn(provider_name="mock", conversation_id="C1")
            await repo.record_provider_call(turn.id, provider_name="mock", latency_ms=120)
            await repo.record_tool_call(turn.id, tool_name="read_file", status=ToolCallStatus.OK)
            await repo.finalize_turn(
                turn.id,
                status=TurnStatus.COMPLETED,
                stop_reason="end_turn",
                input_tokens=20,
                output_tokens=10,
            )

        listed = await _call(server, "list_traces")
        assert len(listed["result"]["turns"]) == 1
        assert listed["result"]["turns"][0]["status"] == "completed"

        shown = await _call(server, "get_trace_turn", {"turn_id": turn.id})
        assert shown["result"]["turn"]["id"] == turn.id
        assert shown["result"]["turn"]["input_tokens"] == 20

        viewed = await _call(server, "view_trace_tree", {"turn_id": turn.id})
        assert len(viewed["result"]["provider_calls"]) == 1
        assert len(viewed["result"]["tool_calls"]) == 1
        assert viewed["result"]["tool_calls"][0]["tool_name"] == "read_file"

    async def test_show_not_found(self, server: JsonRpcServer) -> None:
        line = await _call(server, "get_trace_turn", {"turn_id": "ghost"})
        assert line["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND

    async def test_view_not_found(self, server: JsonRpcServer) -> None:
        line = await _call(server, "view_trace_tree", {"turn_id": "ghost"})
        assert line["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND

    async def test_list_filter_by_conversation(self, server: JsonRpcServer, agent: AIAgent) -> None:
        async with agent.session_maker() as session:
            repo = TraceRepo(session)
            await repo.create_turn(provider_name="mock", conversation_id="A")
            await repo.create_turn(provider_name="mock", conversation_id="B")

        line = await _call(server, "list_traces", {"conversation_id": "A"})
        turns = line["result"]["turns"]
        assert len(turns) == 1
        assert turns[0]["conversation_id"] == "A"

    async def test_list_filter_invalid_status(self, server: JsonRpcServer) -> None:
        line = await _call(server, "list_traces", {"status": "bogus"})
        assert line["error"]["code"] == JsonRpcServer.ERR_INVALID_PARAMS

    async def test_reconcile_returns_zero_when_no_stale(self, server: JsonRpcServer) -> None:
        line = await _call(server, "reconcile_traces", {"older_than_seconds": 60})
        assert line["result"] == {"cleaned": 0}

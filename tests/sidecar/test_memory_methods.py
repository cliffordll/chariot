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
async def test_memory_methods_round_trip(server: JsonRpcServer) -> None:
    add = await _call(
        server,
        "create_memory",
        {
            "kind": "preference",
            "text": "Reply in Chinese.",
            "meta": {"scope": "user"},
            "links": [{"link_type": "conversation", "link_value": "01H_MEMORY_CONVO"}],
        },
    )
    memory_id = add["result"]["memory"]["id"]

    listed = await _call(server, "list_memories")
    assert listed["result"]["entries"][0]["id"] == memory_id

    shown = await _call(server, "get_memory", {"memory_id": memory_id})
    assert shown["result"]["memory"]["kind"] == "preference"

    pinned = await _call(server, "pin_memory", {"memory_id": memory_id})
    assert pinned["result"]["memory"]["pinned"] is True

    archived = await _call(server, "archive_memory", {"memory_id": memory_id})
    assert archived["result"]["memory"]["archived"] is True

    events = await _call(server, "list_memory_events", {"memory_id": memory_id})
    assert events["result"]["events"][0]["event_type"] in {"updated", "archived", "created"}

    links = await _call(server, "list_memory_links", {"memory_id": memory_id})
    assert links["result"]["links"][0]["link_type"] == "conversation"

    search = await _call(server, "search_memory", {"query": "Chinese"})
    assert search["result"]["entries"][0]["id"] == memory_id

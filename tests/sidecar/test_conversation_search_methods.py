"""B3 wave 1 — sidecar `search_conversation` / `rebuild_conversation_fts` RPC dispatch。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from chariot.agent.registry import AgentRegistry
from chariot.agent.run import AIAgent
from chariot.database.session import dispose_db
from chariot.repos.conversation_repo import ConversationRepo
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


@pytest_asyncio.fixture
async def agent(tmp_path: Path):
    a = await AIAgent.bootstrap(tmp_path / "test.db")
    AgentRegistry._agents.clear()
    try:
        yield a
    finally:
        AgentRegistry._agents.clear()
        await dispose_db()


@pytest.fixture
def server(agent: AIAgent, tmp_path: Path) -> JsonRpcServer:
    s = JsonRpcServer()
    register_methods(s, agent, db_path=tmp_path / "test.db")
    return s


async def _call(server: JsonRpcServer, method: str, params: dict[str, Any]) -> dict[str, Any]:
    writer = MockWriter()
    await server.serve(make_reader(_request_frame(1, method, params)), writer)
    return writer.lines()[0]


@pytest.mark.asyncio
async def test_search_conversation_returns_hits(server: JsonRpcServer, agent: AIAgent) -> None:
    """RPC happy path:写一条 message,search 命中。"""
    async with agent.session_maker() as session:
        repo = ConversationRepo(session)
        await repo.create("01CONV_A")
        await repo.append_message("01CONV_A", "user", "fts5 search demo")

    resp = await _call(server, "search_conversation", {"query": "fts5"})
    assert "result" in resp
    hits = resp["result"]["hits"]
    assert len(hits) == 1
    assert hits[0]["conversation_id"] == "01CONV_A"
    assert hits[0]["role"] == "user"
    assert "rank" in hits[0]


@pytest.mark.asyncio
async def test_search_conversation_filter_by_conversation(server: JsonRpcServer, agent: AIAgent) -> None:
    async with agent.session_maker() as session:
        repo = ConversationRepo(session)
        await repo.create("01CONV_A")
        await repo.create("01CONV_B")
        await repo.append_message("01CONV_A", "user", "alpha keyword")
        await repo.append_message("01CONV_B", "user", "alpha keyword")

    all_resp = await _call(server, "search_conversation", {"query": "alpha"})
    a_resp = await _call(server, "search_conversation", {"query": "alpha", "conversation_id": "01CONV_A"})
    assert len(all_resp["result"]["hits"]) == 2
    assert len(a_resp["result"]["hits"]) == 1
    assert a_resp["result"]["hits"][0]["conversation_id"] == "01CONV_A"


@pytest.mark.asyncio
async def test_search_conversation_missing_query_errors(server: JsonRpcServer) -> None:
    resp = await _call(server, "search_conversation", {})
    assert resp["error"]["code"] == JsonRpcServer.ERR_INVALID_PARAMS


@pytest.mark.asyncio
async def test_rebuild_conversation_fts_returns_count(server: JsonRpcServer, agent: AIAgent) -> None:
    async with agent.session_maker() as session:
        repo = ConversationRepo(session)
        await repo.create("01CONV")
        await repo.append_message("01CONV", "user", "one")
        await repo.append_message("01CONV", "assistant", "two", provider_name="mock")

    resp = await _call(server, "rebuild_conversation_fts", {})
    assert resp["result"]["rebuilt"] == 2


@pytest.mark.asyncio
async def test_search_conversation_limit(server: JsonRpcServer, agent: AIAgent) -> None:
    async with agent.session_maker() as session:
        repo = ConversationRepo(session)
        await repo.create("01CONV")
        for i in range(5):
            await repo.append_message("01CONV", "user", f"target_{i} match")

    resp = await _call(server, "search_conversation", {"query": "match", "limit": 2})
    assert len(resp["result"]["hits"]) == 2

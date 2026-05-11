"""B3 wave 2 — sidecar `*_auxiliary_client` RPC dispatch。"""

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


async def test_list_includes_seeded_summarizer(server: JsonRpcServer) -> None:
    resp = await _call(server, "list_auxiliary_clients", {})
    assert "result" in resp
    names = [e["name"] for e in resp["result"]["auxiliary_clients"]]
    assert "summarizer" in names


async def test_show_returns_entry(server: JsonRpcServer) -> None:
    resp = await _call(server, "show_auxiliary_client", {"name": "summarizer"})
    assert "result" in resp
    aux = resp["result"]["auxiliary_client"]
    assert aux["provider_entry"] == "mock"


async def test_show_unknown_returns_not_found(server: JsonRpcServer) -> None:
    resp = await _call(server, "show_auxiliary_client", {"name": "ghost"})
    assert "error" in resp
    assert resp["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND


async def test_add_then_delete(server: JsonRpcServer) -> None:
    add_resp = await _call(
        server,
        "add_auxiliary_client",
        {"name": "critic_aux", "provider_entry": "mock", "model": "mock-critic"},
    )
    assert add_resp["result"]["auxiliary_client"]["model"] == "mock-critic"

    dup_resp = await _call(
        server,
        "add_auxiliary_client",
        {"name": "critic_aux", "provider_entry": "mock"},
    )
    assert dup_resp["error"]["code"] == JsonRpcServer.ERR_DUPLICATE

    del_resp = await _call(server, "delete_auxiliary_client", {"name": "critic_aux"})
    assert del_resp["result"]["deleted"] == "critic_aux"


async def test_update_clear_model_via_null(server: JsonRpcServer) -> None:
    await _call(
        server,
        "add_auxiliary_client",
        {"name": "aux1", "provider_entry": "mock", "model": "mock-1"},
    )
    resp = await _call(
        server,
        "update_auxiliary_client",
        {"name": "aux1", "model": None},
    )
    assert resp["result"]["auxiliary_client"]["model"] is None


async def test_update_unset_model_keeps_value(server: JsonRpcServer) -> None:
    """model 字段不传 → UNSET → 不动。"""
    await _call(
        server,
        "add_auxiliary_client",
        {"name": "aux1", "provider_entry": "mock", "model": "mock-1"},
    )
    resp = await _call(
        server,
        "update_auxiliary_client",
        {"name": "aux1", "params": {"max_tokens": 2048}},
    )
    aux = resp["result"]["auxiliary_client"]
    assert aux["model"] == "mock-1"
    assert aux["params"] == {"max_tokens": 2048}


async def test_add_missing_provider_entry_errors(server: JsonRpcServer) -> None:
    resp = await _call(server, "add_auxiliary_client", {"name": "x"})
    assert resp["error"]["code"] == JsonRpcServer.ERR_INVALID_PARAMS

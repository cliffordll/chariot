"""B5 wave 2 —— sidecar `list_audit_events` / `get_audit_event` + memory_store hook。"""

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
from chariot.audit.hooks import AuditHookManager
from chariot.database.session import dispose_db
from chariot.rpc.jsonrpc import JsonRpcServer
from chariot.services.audit import AuditService
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


def _frame(rid: int, method: str, params: dict[str, Any] | None = None) -> bytes:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        body["params"] = params
    return (json.dumps(body) + "\n").encode()


@pytest_asyncio.fixture
async def agent(tmp_path: Path) -> AsyncIterator[AIAgent]:
    a = await AIAgent.bootstrap(tmp_path / "audit.db")
    AgentRegistry._agents.clear()
    try:
        yield a
    finally:
        AgentRegistry._agents.clear()
        await dispose_db()


@pytest.fixture
def server(agent: AIAgent, tmp_path: Path) -> JsonRpcServer:
    s = JsonRpcServer()
    register_methods(s, agent, db_path=tmp_path / "audit.db")
    return s


async def _call(server: JsonRpcServer, method: str, params: dict[str, Any]) -> dict[str, Any]:
    writer = MockWriter()
    await server.serve(make_reader(_frame(1, method, params)), writer)
    return writer.lines()[0]


async def test_list_audit_events_empty(server: JsonRpcServer) -> None:
    resp = await _call(server, "list_audit_events", {})
    assert "result" in resp
    assert resp["result"]["events"] == []


async def test_create_memory_records_audit(server: JsonRpcServer) -> None:
    """create_memory RPC → memory_store audit event。"""
    resp = await _call(
        server,
        "create_memory",
        {"kind": "preference", "text": "use Chinese in chat"},
    )
    assert "result" in resp
    memory_id = resp["result"]["memory"]["id"]

    # 查 audit
    list_resp = await _call(server, "list_audit_events", {"limit": 10})
    events = list_resp["result"]["events"]
    assert any(
        ev["event_type"] == AuditHookManager.EVENT_MEMORY_STORE
        and ev["payload"]["memory_id"] == memory_id
        and ev["payload"]["action"] == "create"
        for ev in events
    )


async def test_pin_memory_records_audit(server: JsonRpcServer) -> None:
    create = await _call(server, "create_memory", {"kind": "preference", "text": "x"})
    memory_id = create["result"]["memory"]["id"]
    pin = await _call(server, "pin_memory", {"memory_id": memory_id, "pinned": True})
    assert "result" in pin

    list_resp = await _call(server, "list_audit_events", {"limit": 10})
    events = list_resp["result"]["events"]
    actions = [
        ev["payload"]["action"]
        for ev in events
        if ev["event_type"] == AuditHookManager.EVENT_MEMORY_STORE and ev["payload"]["memory_id"] == memory_id
    ]
    assert "create" in actions
    assert "pin" in actions


async def test_get_audit_event_returns_payload(
    server: JsonRpcServer,
    agent: AIAgent,
) -> None:
    await _call(server, "create_memory", {"kind": "preference", "text": "y"})
    # 拿最新一条 audit event
    events = await AuditService(agent).list_events(limit=10)
    assert events
    event_id = events[0].id

    resp = await _call(server, "get_audit_event", {"event_id": event_id})
    assert "result" in resp
    assert resp["result"]["event"]["id"] == event_id
    assert resp["result"]["event"]["event_type"] in {
        AuditHookManager.EVENT_MEMORY_STORE,
    }


async def test_get_audit_event_missing_returns_not_found(server: JsonRpcServer) -> None:
    resp = await _call(server, "get_audit_event", {"event_id": "does-not-exist"})
    assert resp["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND

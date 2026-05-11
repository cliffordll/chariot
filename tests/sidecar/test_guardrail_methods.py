"""B5 wave 1 — sidecar `list_guardrails` / `try_guardrail` RPC dispatch。"""

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


def _frame(rid: int, method: str, params: dict[str, Any] | None = None) -> bytes:
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
    await server.serve(make_reader(_frame(1, method, params)), writer)
    return writer.lines()[0]


async def test_list_guardrails_returns_13(server: JsonRpcServer) -> None:
    resp = await _call(server, "list_guardrails", {})
    assert "result" in resp
    rules = resp["result"]["rules"]
    assert len(rules) == 13
    # 每条都有必需字段
    for r in rules:
        assert "rule_id" in r
        assert "verdict" in r
        assert "description" in r
        assert "daily_quota" in r
        assert "quota_remaining" in r


async def test_try_guardrail_deny(server: JsonRpcServer) -> None:
    resp = await _call(
        server,
        "try_guardrail",
        {"tool_name": "shell_exec", "args": {"command": "rm -rf /"}},
    )
    assert "result" in resp
    assert resp["result"]["verdict"] == "deny"
    assert resp["result"]["rule_id"] == "shell_rm_rf"
    assert resp["result"]["matched_pattern"] is not None


async def test_try_guardrail_require_approval(server: JsonRpcServer) -> None:
    resp = await _call(
        server,
        "try_guardrail",
        {"tool_name": "shell_exec", "args": {"command": "chmod 777 /opt"}},
    )
    assert resp["result"]["verdict"] == "require_approval"
    assert resp["result"]["rule_id"] == "shell_chmod_unsafe"


async def test_try_guardrail_allow(server: JsonRpcServer) -> None:
    resp = await _call(
        server,
        "try_guardrail",
        {"tool_name": "read_file", "args": {"path": "README.md"}},
    )
    assert resp["result"]["verdict"] == "allow"
    assert resp["result"]["rule_id"] == "_default"


async def test_try_guardrail_does_not_consume_quota(server: JsonRpcServer) -> None:
    """preview 多次 quota 不减。"""
    for _ in range(10):
        resp = await _call(
            server,
            "try_guardrail",
            {"tool_name": "shell_exec", "args": {"command": "chmod 777 /opt"}},
        )
        assert resp["result"]["verdict"] == "require_approval"
        # 配额仍是 5(未被消耗)
        assert resp["result"]["quota_remaining"] == 5


async def test_try_guardrail_missing_tool_name_errors(server: JsonRpcServer) -> None:
    resp = await _call(server, "try_guardrail", {})
    assert resp["error"]["code"] == JsonRpcServer.ERR_INVALID_PARAMS

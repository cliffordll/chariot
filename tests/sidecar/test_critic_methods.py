"""B4 wave 1 — sidecar `critique_text` / `get_critic_config` RPC dispatch。"""

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
from chariot.services.auxiliary import AuxiliaryService
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
async def agent_no_critic(tmp_path: Path):
    """Agent bootstrap 不装 critic(auxiliary_clients 表只有 default summarizer)。"""
    a = await AIAgent.bootstrap(tmp_path / "test.db")
    AgentRegistry._agents.clear()
    try:
        yield a
    finally:
        AgentRegistry._agents.clear()
        await dispose_db()


@pytest_asyncio.fixture
async def agent_with_critic(tmp_path: Path):
    """先插入 critic auxiliary entry,再 bootstrap,装载 critic。"""
    # 第一次 bootstrap 跑 migrations + seed
    a = await AIAgent.bootstrap(tmp_path / "test.db")
    await AuxiliaryService(a).create(name="critic", provider_entry="mock")
    # 第二次 bootstrap 把 critic 装上(同一 sm 复用,但 dispose 后重建 agent)
    AgentRegistry._agents.clear()
    await dispose_db()
    a2 = await AIAgent.bootstrap(tmp_path / "test.db")
    AgentRegistry._agents.clear()
    try:
        yield a2
    finally:
        AgentRegistry._agents.clear()
        await dispose_db()


@pytest.fixture
def server_no_critic(agent_no_critic: AIAgent, tmp_path: Path) -> JsonRpcServer:
    s = JsonRpcServer()
    register_methods(s, agent_no_critic, db_path=tmp_path / "test.db")
    return s


@pytest.fixture
def server_with_critic(agent_with_critic: AIAgent, tmp_path: Path) -> JsonRpcServer:
    s = JsonRpcServer()
    register_methods(s, agent_with_critic, db_path=tmp_path / "test.db")
    return s


async def _call(server: JsonRpcServer, method: str, params: dict[str, Any]) -> dict[str, Any]:
    writer = MockWriter()
    await server.serve(make_reader(_frame(1, method, params)), writer)
    return writer.lines()[0]


# ---- get_critic_config ----


async def test_get_critic_config_loaded_false_when_no_critic_row(server_no_critic: JsonRpcServer) -> None:
    resp = await _call(server_no_critic, "get_critic_config", {})
    assert resp["result"]["loaded"] is False


async def test_get_critic_config_loaded_true_when_critic_row_present(server_with_critic: JsonRpcServer) -> None:
    resp = await _call(server_with_critic, "get_critic_config", {})
    assert resp["result"]["loaded"] is True
    assert resp["result"]["auxiliary_client"]["name"] == "critic"
    assert resp["result"]["auxiliary_client"]["provider_entry"] == "mock"


# ---- critique_text ----


async def test_critique_text_returns_verdict(server_with_critic: JsonRpcServer) -> None:
    """mock provider echo,不会含 VERDICT → parser 兜底 UNSURE,但 RPC 形态完整。"""
    resp = await _call(
        server_with_critic,
        "critique_text",
        {"task_goal": "sort", "produced": "def sort(x): return x"},
    )
    assert "result" in resp
    assert resp["result"]["verdict"] in {"PASS", "FAIL", "UNSURE"}
    assert isinstance(resp["result"]["reason"], str)
    assert isinstance(resp["result"]["raw"], str)


async def test_critique_text_returns_not_found_without_critic(server_no_critic: JsonRpcServer) -> None:
    resp = await _call(
        server_no_critic,
        "critique_text",
        {"task_goal": "x", "produced": "y"},
    )
    assert resp["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND


async def test_critique_text_missing_task_goal_errors(server_with_critic: JsonRpcServer) -> None:
    resp = await _call(server_with_critic, "critique_text", {"produced": "y"})
    assert resp["error"]["code"] == JsonRpcServer.ERR_INVALID_PARAMS


async def test_critique_text_missing_produced_errors(server_with_critic: JsonRpcServer) -> None:
    resp = await _call(server_with_critic, "critique_text", {"task_goal": "x"})
    assert resp["error"]["code"] == JsonRpcServer.ERR_INVALID_PARAMS


async def test_critique_text_optional_context(server_with_critic: JsonRpcServer) -> None:
    resp = await _call(
        server_with_critic,
        "critique_text",
        {
            "task_goal": "x",
            "produced": "y",
            "extra_context": "some extra background",
        },
    )
    assert "result" in resp

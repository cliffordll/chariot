"""B7 wave 1 —— sidecar `export_trajectory` RPC dispatch + 落盘。"""

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
from chariot.models.trace import TurnStatus
from chariot.repos.conversation_repo import ConversationRepo
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


def _frame(rid: int, method: str, params: dict[str, Any] | None = None) -> bytes:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        body["params"] = params
    return (json.dumps(body) + "\n").encode()


@pytest_asyncio.fixture
async def agent(tmp_path: Path) -> AsyncIterator[AIAgent]:
    a = await AIAgent.bootstrap(tmp_path / "rl_methods.db")
    AgentRegistry._agents.clear()
    try:
        # 落一条 conversation 给 export 用
        async with a.session_maker() as session:
            repo = ConversationRepo(session)
            await repo.ensure_exists("cv-rl")
            await repo.append_message("cv-rl", role="user", content=[{"type": "text", "text": "hello"}])
            turn = await TraceRepo(session).create_turn(
                conversation_id="cv-rl",
                provider_name="mock",
            )
            await repo.append_message("cv-rl", role="assistant", content=[{"type": "text", "text": "hi"}])
            await TraceRepo(session).finalize_turn(
                turn.id,
                status=TurnStatus.COMPLETED,
                stop_reason="end_turn",
            )
        yield a
    finally:
        AgentRegistry._agents.clear()
        await dispose_db()


@pytest.fixture
def server(agent: AIAgent, tmp_path: Path) -> JsonRpcServer:
    s = JsonRpcServer()
    register_methods(s, agent, db_path=tmp_path / "rl_methods.db")
    return s


async def _call(server: JsonRpcServer, method: str, params: dict[str, Any]) -> dict[str, Any]:
    writer = MockWriter()
    await server.serve(make_reader(_frame(1, method, params)), writer)
    return writer.lines()[0]


# ---- export_trajectory ----


async def test_export_trajectory_default_path(server: JsonRpcServer, tmp_path: Path) -> None:
    out = tmp_path / "rl_out.jsonl"
    resp = await _call(
        server,
        "export_trajectory",
        {"conversation_id": "cv-rl", "out_path": str(out)},
    )
    assert "result" in resp
    result = resp["result"]
    assert result["row_count"] == 1
    assert result["out_path"] == str(out)
    assert result["scrub_mode"] == "default"
    assert out.exists()


async def test_export_trajectory_raw_mode(server: JsonRpcServer, tmp_path: Path) -> None:
    out = tmp_path / "rl_raw.jsonl"
    resp = await _call(
        server,
        "export_trajectory",
        {"conversation_id": "cv-rl", "out_path": str(out), "raw": True},
    )
    assert resp["result"]["scrub_mode"] == "raw"


async def test_export_trajectory_unknown_conversation(server: JsonRpcServer, tmp_path: Path) -> None:
    """未知 conv → row_count=0(不报错,空文件 / 空列表)。"""
    out = tmp_path / "empty.jsonl"
    resp = await _call(
        server,
        "export_trajectory",
        {"conversation_id": "does-not-exist", "out_path": str(out)},
    )
    assert resp["result"]["row_count"] == 0


async def test_export_trajectory_requires_conversation_id(server: JsonRpcServer) -> None:
    resp = await _call(server, "export_trajectory", {})
    assert "error" in resp
    assert resp["error"]["code"] == JsonRpcServer.ERR_INVALID_PARAMS

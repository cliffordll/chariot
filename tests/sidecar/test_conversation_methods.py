"""sidecar conversation RPC dispatch tests(0.6.5 S.8c)。

覆盖:update_conversation_config / 已有 list/get/rename/delete 的基础链路。
"""

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
from chariot.services.conversation import ConversationService
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


# ---- update_conversation_config ----


@pytest.mark.asyncio
async def test_update_conversation_config_updates_agent(server: JsonRpcServer, agent: AIAgent) -> None:
    """RPC happy path:更新 agent_profile,返回最新 conversation。"""
    await ConversationService(agent).create("01CONV")

    resp = await _call(
        server,
        "update_conversation_config",
        {
            "conversation_id": "01CONV",
            "agent_profile": "planner",
        },
    )

    assert "result" in resp
    conv = resp["result"]["conversation"]
    assert conv["agent_profile"] == "planner"


@pytest.mark.asyncio
async def test_update_conversation_config_keeps_agent_when_not_given(server: JsonRpcServer, agent: AIAgent) -> None:
    """不传 agent_profile,保持原值。"""
    service = ConversationService(agent)
    await service.create("01CONV")
    await service.update_config("01CONV", agent_profile="old-agent")

    resp = await _call(
        server,
        "update_conversation_config",
        {
            "conversation_id": "01CONV",
        },
    )

    conv = resp["result"]["conversation"]
    assert conv["agent_profile"] == "old-agent"


@pytest.mark.asyncio
async def test_update_conversation_config_updates_only_agent(server: JsonRpcServer, agent: AIAgent) -> None:
    """只传 agent_profile,覆盖原值。"""
    service = ConversationService(agent)
    await service.create("01CONV")
    await service.update_config("01CONV", agent_profile="old-agent")

    resp = await _call(
        server,
        "update_conversation_config",
        {
            "conversation_id": "01CONV",
            "agent_profile": "new-agent",
        },
    )

    conv = resp["result"]["conversation"]
    assert conv["agent_profile"] == "new-agent"


@pytest.mark.asyncio
async def test_update_conversation_config_not_found(server: JsonRpcServer) -> None:
    """更新不存在的 conversation → 404。"""
    resp = await _call(
        server,
        "update_conversation_config",
        {
            "conversation_id": "NONEXISTENT",
            "last_provider": "x",
        },
    )

    assert "error" in resp
    assert resp["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND


# ---- get_conversation (含消息列表) ----


@pytest.mark.asyncio
async def test_get_conversation_returns_messages(server: JsonRpcServer, agent: AIAgent) -> None:
    """get_conversation 返回 conversation + messages。"""
    service = ConversationService(agent)
    await service.create("01CONV")
    await service.append_user_message("01CONV", "hello")
    await service.append_assistant_message(
        "01CONV",
        [{"type": "text", "text": "hi"}],
        provider_name="mock",
    )

    resp = await _call(server, "get_conversation", {"conversation_id": "01CONV"})
    assert "result" in resp
    assert resp["result"]["conversation"]["id"] == "01CONV"
    msgs = resp["result"]["messages"]
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_get_conversation_not_found(server: JsonRpcServer) -> None:
    resp = await _call(server, "get_conversation", {"conversation_id": "NONEXISTENT"})
    assert "error" in resp
    assert resp["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND


# ---- rename_conversation ----


@pytest.mark.asyncio
async def test_rename_conversation(server: JsonRpcServer, agent: AIAgent) -> None:
    await ConversationService(agent).create("01CONV", title="old")

    resp = await _call(
        server,
        "rename_conversation",
        {
            "conversation_id": "01CONV",
            "title": "new",
        },
    )
    assert resp["result"]["conversation"]["title"] == "new"


# ---- delete_conversation ----


@pytest.mark.asyncio
async def test_delete_conversation(server: JsonRpcServer, agent: AIAgent) -> None:
    await ConversationService(agent).create("01CONV")

    resp = await _call(server, "delete_conversation", {"conversation_id": "01CONV"})
    assert resp["result"]["deleted"] == "01CONV"

    get_resp = await _call(server, "get_conversation", {"conversation_id": "01CONV"})
    assert "error" in get_resp

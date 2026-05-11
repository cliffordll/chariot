"""sidecar toolset RPC dispatch tests."""

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


class TestToolsetMethods:
    async def test_list_empty(self, server: JsonRpcServer) -> None:
        line = await _call(server, "list_toolsets")
        assert line["result"] == {"toolsets": []}

    async def test_create_and_show(self, server: JsonRpcServer) -> None:
        created = await _call(
            server,
            "create_toolset",
            {"name": "fs_safe", "description": "只读", "members": ["read_file", "list_dir"]},
        )
        assert created["result"]["toolset"]["name"] == "fs_safe"
        assert created["result"]["toolset"]["members"] == ["list_dir", "read_file"]

        shown = await _call(server, "get_toolset", {"name": "fs_safe"})
        assert shown["result"]["toolset"]["description"] == "只读"

    async def test_show_not_found(self, server: JsonRpcServer) -> None:
        line = await _call(server, "get_toolset", {"name": "ghost"})
        assert line["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND

    async def test_create_duplicate(self, server: JsonRpcServer) -> None:
        await _call(server, "create_toolset", {"name": "dup"})
        line = await _call(server, "create_toolset", {"name": "dup"})
        # ConfigError -> INVALID_PARAMS per MethodBase._session
        assert line["error"]["code"] == JsonRpcServer.ERR_INVALID_PARAMS

    async def test_update_members(self, server: JsonRpcServer) -> None:
        await _call(server, "create_toolset", {"name": "ts", "members": ["read_file"]})
        updated = await _call(server, "update_toolset", {"name": "ts", "members": ["list_dir", "http_get"]})
        assert updated["result"]["toolset"]["members"] == ["http_get", "list_dir"]

    async def test_add_remove_member(self, server: JsonRpcServer) -> None:
        await _call(server, "create_toolset", {"name": "ts"})
        await _call(server, "add_toolset_member", {"name": "ts", "tool_name": "read_file"})
        with_member = await _call(server, "get_toolset", {"name": "ts"})
        assert with_member["result"]["toolset"]["members"] == ["read_file"]

        await _call(server, "remove_toolset_member", {"name": "ts", "tool_name": "read_file"})
        cleared = await _call(server, "get_toolset", {"name": "ts"})
        assert cleared["result"]["toolset"]["members"] == []

    async def test_delete(self, server: JsonRpcServer) -> None:
        await _call(server, "create_toolset", {"name": "ts"})
        line = await _call(server, "delete_toolset", {"name": "ts"})
        assert line["result"] == {"deleted": "ts"}

        gone = await _call(server, "get_toolset", {"name": "ts"})
        assert gone["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND

    async def test_members_invalid_type(self, server: JsonRpcServer) -> None:
        line = await _call(server, "create_toolset", {"name": "ts", "members": "not-a-list"})
        assert line["error"]["code"] == JsonRpcServer.ERR_INVALID_PARAMS

"""sidecar admin methods 单测(0.6.5 S.8c)。

跟 `test_chat_method.py` 形态一致(都通过 JsonRpcServer dispatch 走端到端),
但用真 AIAgent.bootstrap + tmp DB(而非 mock agent)—— 因为 admin methods
需要真 session_maker / repos 才能跑 CRUD。

覆盖 14 个 method 各自的 happy path + 关键边界:

- list_convos / get_convo / rename_convo / delete_convo
- list_tools / enable_tool / disable_tool / config_tool
- list_providers / add_provider / edit_provider / delete_provider / probe_provider
- list_logs

错误码映射:NOT_FOUND / DUPLICATE / INVALID_PARAMS 各 1-2 个 case。
"""

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
from chariot.repos.convo_repo import ConvoRepo
from chariot.repos.log_repo import LogRepo
from chariot.rpc.jsonrpc import JsonRpcServer
from chariot.sidecar.methods import register_methods

# ---------------------------------------------------------------------------
# 测试基础设施
# ---------------------------------------------------------------------------


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


async def _call(
    server: JsonRpcServer, method: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    """跑一次 RPC,返回第一帧解析(response 或 error)。"""
    reader = make_reader(_request_frame(1, method, params))
    writer = MockWriter()
    await server.serve(reader, writer)
    return writer.lines()[0]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def _isolate() -> AsyncIterator[None]:
    """每 test 前后清 AgentRegistry + DB engine,test 间无串台。"""
    await AgentRegistry.clear()
    await dispose_db()
    yield
    await AgentRegistry.clear()
    await dispose_db()


@pytest_asyncio.fixture
async def agent(tmp_path: Path) -> AIAgent:
    """每 test 一个全新 DB + 全新 AIAgent;seed 跑完后含 mock entry + 4 disabled tool。"""
    db_path = tmp_path / "chariot.db"
    return await AIAgent.bootstrap(db_path)


@pytest.fixture
def server(agent: AIAgent) -> JsonRpcServer:
    """已注册全套 method 的 JsonRpcServer 实例。"""
    s = JsonRpcServer()
    register_methods(s, agent)
    return s


# ---------------------------------------------------------------------------
# convo methods
# ---------------------------------------------------------------------------


class TestConvoMethods:
    async def test_list_convos_empty_initial(self, server: JsonRpcServer) -> None:
        line = await _call(server, "list_convos")
        assert line["result"] == {"convos": []}

    async def test_list_convos_returns_seeded(self, server: JsonRpcServer, agent: AIAgent) -> None:
        """先用 repo 直接插入 2 个 convo,再走 RPC list 验证。"""
        async with agent.session_maker() as session:
            repo = ConvoRepo(session)
            await repo.create("01H_TEST_A", title="first")
            await repo.create("01H_TEST_B", title="second")

        line = await _call(server, "list_convos")
        ids = sorted(c["id"] for c in line["result"]["convos"])
        assert ids == ["01H_TEST_A", "01H_TEST_B"]

    async def test_get_convo_returns_messages(self, server: JsonRpcServer, agent: AIAgent) -> None:
        async with agent.session_maker() as session:
            repo = ConvoRepo(session)
            await repo.create("01H_C", title="t")
            await repo.append_message("01H_C", role="user", content="hi")

        line = await _call(server, "get_convo", {"convo_id": "01H_C"})
        result = line["result"]
        assert result["convo"]["id"] == "01H_C"
        assert len(result["messages"]) == 1
        assert result["messages"][0]["role"] == "user"

    async def test_get_convo_not_found_returns_err_not_found(self, server: JsonRpcServer) -> None:
        line = await _call(server, "get_convo", {"convo_id": "ghost"})
        assert line["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND

    async def test_rename_convo(self, server: JsonRpcServer, agent: AIAgent) -> None:
        async with agent.session_maker() as session:
            await ConvoRepo(session).create("01H_R", title="old")

        line = await _call(server, "rename_convo", {"convo_id": "01H_R", "title": "new"})
        assert line["result"]["convo"]["title"] == "new"

    async def test_rename_convo_not_found(self, server: JsonRpcServer) -> None:
        line = await _call(server, "rename_convo", {"convo_id": "ghost", "title": "x"})
        assert line["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND

    async def test_delete_convo(self, server: JsonRpcServer, agent: AIAgent) -> None:
        async with agent.session_maker() as session:
            await ConvoRepo(session).create("01H_D", title="t")

        line = await _call(server, "delete_convo", {"convo_id": "01H_D"})
        assert line["result"] == {"deleted": "01H_D"}

        # 再次 get 应该 not found
        line2 = await _call(server, "get_convo", {"convo_id": "01H_D"})
        assert line2["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND

    async def test_convo_id_required(self, server: JsonRpcServer) -> None:
        """params 缺 convo_id → ERR_INVALID_PARAMS。"""
        line = await _call(server, "get_convo", {})
        assert line["error"]["code"] == JsonRpcServer.ERR_INVALID_PARAMS


# ---------------------------------------------------------------------------
# tool methods
# ---------------------------------------------------------------------------


class TestToolMethods:
    async def test_list_tools_returns_4_seeded(self, server: JsonRpcServer) -> None:
        """seed_if_empty 装 4 条 fixture(read_file / list_dir / shell_exec / http_get),
        全 disabled。"""
        line = await _call(server, "list_tools")
        tools = line["result"]["tools"]
        assert len(tools) == 4
        names = sorted(t["name"] for t in tools)
        assert names == ["http_get", "list_dir", "read_file", "shell_exec"]
        # 默认全 disabled
        assert all(not t["enabled"] for t in tools)

    async def test_enable_tool(self, server: JsonRpcServer) -> None:
        line = await _call(server, "enable_tool", {"name": "list_dir"})
        assert line["result"]["tool"]["enabled"] is True

    async def test_disable_tool(self, server: JsonRpcServer) -> None:
        await _call(server, "enable_tool", {"name": "list_dir"})
        line = await _call(server, "disable_tool", {"name": "list_dir"})
        assert line["result"]["tool"]["enabled"] is False

    async def test_config_tool_options(self, server: JsonRpcServer) -> None:
        line = await _call(
            server, "config_tool", {"name": "read_file", "options": {"max_bytes": 8192}}
        )
        assert line["result"]["tool"]["options"] == {"max_bytes": 8192}

    async def test_enable_unknown_tool_returns_not_found(self, server: JsonRpcServer) -> None:
        line = await _call(server, "enable_tool", {"name": "no_such_tool"})
        assert line["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND


# ---------------------------------------------------------------------------
# provider methods
# ---------------------------------------------------------------------------


class TestProviderMethods:
    async def test_list_providers_returns_seeded_mock(self, server: JsonRpcServer) -> None:
        """seed_if_empty 插入一个 mock entry。"""
        line = await _call(server, "list_providers")
        providers = line["result"]["providers"]
        assert len(providers) == 1
        assert providers[0]["name"] == "mock"
        assert providers[0]["type"] == "mock"
        # seed_if_empty 只插入 entry,不设默认(is_default=0)。是 CLI/UI 操作显式 set
        assert providers[0]["default"] is False

    async def test_add_provider(self, server: JsonRpcServer) -> None:
        line = await _call(
            server,
            "add_provider",
            {
                "name": "mock2",
                "type": "mock",
                "options": {},
                "params": {},
            },
        )
        result = line["result"]["provider"]
        assert result["name"] == "mock2"
        assert result["type"] == "mock"

    async def test_add_provider_duplicate_returns_err_duplicate(
        self, server: JsonRpcServer
    ) -> None:
        """seed 已经有 'mock',再加同名 → ERR_DUPLICATE。"""
        line = await _call(
            server,
            "add_provider",
            {"name": "mock", "type": "mock", "options": {}},
        )
        assert line["error"]["code"] == JsonRpcServer.ERR_DUPLICATE

    async def test_edit_provider(self, server: JsonRpcServer) -> None:
        await _call(
            server,
            "add_provider",
            {"name": "p1", "type": "mock", "options": {"x": 1}},
        )
        line = await _call(
            server,
            "edit_provider",
            {"name": "p1", "options": {"x": 2}},
        )
        assert line["result"]["provider"]["options"] == {"x": 2}

    async def test_edit_provider_not_found(self, server: JsonRpcServer) -> None:
        line = await _call(server, "edit_provider", {"name": "ghost", "options": {}})
        assert line["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND

    async def test_delete_provider(self, server: JsonRpcServer) -> None:
        await _call(server, "add_provider", {"name": "p1", "type": "mock", "options": {}})
        line = await _call(server, "delete_provider", {"name": "p1"})
        assert line["result"] == {"deleted": "p1"}

    async def test_delete_provider_not_found(self, server: JsonRpcServer) -> None:
        line = await _call(server, "delete_provider", {"name": "ghost"})
        assert line["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND

    async def test_probe_mock_provider_succeeds(self, server: JsonRpcServer) -> None:
        """seeded mock entry 跑 probe → ok=True(MockProvider 不发 HTTP)。"""
        line = await _call(server, "probe_provider", {"name": "mock"})
        result = line["result"]
        assert result["ok"] is True
        assert result["error"] is None
        assert result["latency_ms"] >= 0

    async def test_probe_unknown_provider_returns_not_found(self, server: JsonRpcServer) -> None:
        line = await _call(server, "probe_provider", {"name": "ghost"})
        assert line["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND


# ---------------------------------------------------------------------------
# log methods
# ---------------------------------------------------------------------------


class TestLogMethods:
    async def test_list_logs_empty_initial(self, server: JsonRpcServer) -> None:
        line = await _call(server, "list_logs")
        assert line["result"] == {"logs": []}

    async def test_list_logs_returns_inserted(self, server: JsonRpcServer, agent: AIAgent) -> None:
        async with agent.session_maker() as session:
            repo = LogRepo(session)
            await repo.create(provider="mock", status="ok", latency_ms=42)
            await repo.create(provider="mock", status="error", error="boom")

        line = await _call(server, "list_logs")
        logs = line["result"]["logs"]
        assert len(logs) == 2
        statuses = sorted(log["status"] for log in logs)
        assert statuses == ["error", "ok"]

    async def test_list_logs_limit(self, server: JsonRpcServer, agent: AIAgent) -> None:
        async with agent.session_maker() as session:
            repo = LogRepo(session)
            for _ in range(5):
                await repo.create(provider="mock", status="ok")

        line = await _call(server, "list_logs", {"limit": 2})
        assert len(line["result"]["logs"]) == 2

    async def test_list_logs_invalid_limit_returns_err_params(self, server: JsonRpcServer) -> None:
        line = await _call(server, "list_logs", {"limit": -1})
        assert line["error"]["code"] == JsonRpcServer.ERR_INVALID_PARAMS

    async def test_list_logs_since_filter(self, server: JsonRpcServer, agent: AIAgent) -> None:
        """since 过滤 — 只返 created_at > since 的行。"""
        async with agent.session_maker() as session:
            repo = LogRepo(session)
            await repo.create(provider="mock", status="ok")

        # 取一个肯定大于现有行 created_at 的 since,应返空
        future_str = "2099-01-01T00:00:00"
        line = await _call(server, "list_logs", {"since": future_str})
        assert line["result"]["logs"] == []

    async def test_list_logs_invalid_iso(self, server: JsonRpcServer) -> None:
        line = await _call(server, "list_logs", {"since": "not a date"})
        assert line["error"]["code"] == JsonRpcServer.ERR_INVALID_PARAMS


# ---------------------------------------------------------------------------
# 跨 method:method not found / 未注册的 method 名
# ---------------------------------------------------------------------------


class TestRegistration:
    async def test_unknown_method_returns_method_not_found(self, server: JsonRpcServer) -> None:
        line = await _call(server, "no_such_method")
        assert line["error"]["code"] == JsonRpcServer.ERR_METHOD_NOT_FOUND

    def test_register_methods_all_15_present(self, server: JsonRpcServer) -> None:
        """register_methods 应该注册 15 个 method 名。"""
        expected = {
            "chat",
            "list_convos",
            "get_convo",
            "rename_convo",
            "delete_convo",
            "list_tools",
            "enable_tool",
            "disable_tool",
            "config_tool",
            "list_providers",
            "add_provider",
            "edit_provider",
            "delete_provider",
            "probe_provider",
            "list_logs",
        }
        assert server.known_methods() == expected

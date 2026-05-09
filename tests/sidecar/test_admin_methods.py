"""sidecar admin methods 单测(0.6.5 S.8c)。

跟 `test_chat_method.py` 形态一致(都通过 JsonRpcServer dispatch 走端到端),
但用真 AIAgent.bootstrap + tmp DB(而非 mock agent)—— 因为 admin methods
需要真 session_maker / repos 才能跑 CRUD。

覆盖 14 个 method 各自的 happy path + 关键边界:

- list_conversations / get_conversation / rename_conversation / delete_conversation
- list_tools / enable_tool / disable_tool / config_tool
- list_providers / add_provider / update_provider / delete_provider / probe_provider
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
from chariot.repos.conversation_repo import ConversationRepo
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
def server(agent: AIAgent, tmp_path: Path) -> JsonRpcServer:
    """已注册全套 method 的 JsonRpcServer 实例。

    `db_path` 给 ChatMethod 用(0.6.6+ per-call override 路径);admin methods
    跑的不走那条路径,但 register_methods 签名要求,这里跟 agent fixture 同源。
    """
    s = JsonRpcServer()
    register_methods(s, agent, db_path=tmp_path / "chariot.db")
    return s


# ---------------------------------------------------------------------------
# conversation methods
# ---------------------------------------------------------------------------


class TestConversationMethods:
    async def test_list_conversations_empty_initial(self, server: JsonRpcServer) -> None:
        line = await _call(server, "list_conversations")
        assert line["result"] == {"conversations": []}

    async def test_list_conversations_returns_seeded(self, server: JsonRpcServer, agent: AIAgent) -> None:
        """先用 repo 直接插入 2 个 conversation,再走 RPC list 验证。"""
        async with agent.session_maker() as session:
            repo = ConversationRepo(session)
            await repo.create("01H_TEST_A", title="first")
            await repo.create("01H_TEST_B", title="second")

        line = await _call(server, "list_conversations")
        ids = sorted(c["id"] for c in line["result"]["conversations"])
        assert ids == ["01H_TEST_A", "01H_TEST_B"]

    async def test_get_conversation_returns_messages(self, server: JsonRpcServer, agent: AIAgent) -> None:
        async with agent.session_maker() as session:
            repo = ConversationRepo(session)
            await repo.create("01H_C", title="t")
            await repo.append_message("01H_C", role="user", content="hi")

        line = await _call(server, "get_conversation", {"conversation_id": "01H_C"})
        result = line["result"]
        assert result["conversation"]["id"] == "01H_C"
        assert len(result["messages"]) == 1
        assert result["messages"][0]["role"] == "user"

    async def test_get_conversation_not_found_returns_err_not_found(self, server: JsonRpcServer) -> None:
        line = await _call(server, "get_conversation", {"conversation_id": "ghost"})
        assert line["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND

    async def test_rename_conversation(self, server: JsonRpcServer, agent: AIAgent) -> None:
        async with agent.session_maker() as session:
            await ConversationRepo(session).create("01H_R", title="old")

        line = await _call(server, "rename_conversation", {"conversation_id": "01H_R", "title": "new"})
        assert line["result"]["conversation"]["title"] == "new"

    async def test_rename_conversation_not_found(self, server: JsonRpcServer) -> None:
        line = await _call(server, "rename_conversation", {"conversation_id": "ghost", "title": "x"})
        assert line["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND

    async def test_delete_conversation(self, server: JsonRpcServer, agent: AIAgent) -> None:
        async with agent.session_maker() as session:
            await ConversationRepo(session).create("01H_D", title="t")

        line = await _call(server, "delete_conversation", {"conversation_id": "01H_D"})
        assert line["result"] == {"deleted": "01H_D"}

        # 再次 get 应该 not found
        line2 = await _call(server, "get_conversation", {"conversation_id": "01H_D"})
        assert line2["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND

    async def test_conversation_id_required(self, server: JsonRpcServer) -> None:
        """params 缺 conversation_id → ERR_INVALID_PARAMS。"""
        line = await _call(server, "get_conversation", {})
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
        # seed_if_empty 首启直接把 mock 设成默认 provider
        assert providers[0]["default"] is True

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

    async def test_update_provider(self, server: JsonRpcServer) -> None:
        await _call(
            server,
            "add_provider",
            {"name": "p1", "type": "mock", "options": {"x": 1}},
        )
        line = await _call(
            server,
            "update_provider",
            {"name": "p1", "options": {"x": 2}},
        )
        assert line["result"]["provider"]["options"] == {"x": 2}

    async def test_update_provider_not_found(self, server: JsonRpcServer) -> None:
        line = await _call(server, "update_provider", {"name": "ghost", "options": {}})
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
            "list_conversations",
            "get_conversation",
            "rename_conversation",
            "delete_conversation",
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
            "update_provider",
            "delete_provider",
            "probe_provider",
            "list_logs",
        }
        assert server.known_methods() == expected


# ---------------------------------------------------------------------------
# Chat per-call override(0.6.6+)
#
# 桌面端 Chat 页对齐 CLI 的 `--base-url` / `--api-key`:RPC params 多接两个
# 可选字段,任一非空 → ChatMethod 走 AgentRegistry.reserve(session_key 由
# (provider_name, sorted options) 的 sha 算出)拿 per-call AIAgent。
# 无 override → 走默认 agent(fixture 直接 bootstrap,不进 registry)。
#
# 验证抓手:`AgentRegistry.size()` —— default agent 不在 registry 里,所以 size
# 完整反映"per-call agent 缓存有多少条"。
# ---------------------------------------------------------------------------


class TestChatPerCallOverride:
    @staticmethod
    def _chat_params(**extra: str) -> dict[str, Any]:
        return {
            "provider_name": "mock",
            "messages": [{"role": "user", "content": "hi"}],
            **extra,
        }

    @staticmethod
    async def _run_chat(server: JsonRpcServer, params: dict[str, Any]) -> dict[str, Any]:
        """跑一次 chat,取 response 帧(中间有 N 个 chat_event notify,response 在最后)。"""
        reader = make_reader(_request_frame(1, "chat", params))
        writer = MockWriter()
        await server.serve(reader, writer)
        responses = [line for line in writer.lines() if "result" in line]
        assert len(responses) == 1, f"expected 1 response, got {len(responses)}"
        return responses[0]

    async def test_no_override_skips_registry(self, server: JsonRpcServer) -> None:
        """无 base_url / api_key → ChatMethod 走 default agent,不动 AgentRegistry。"""
        await self._run_chat(server, self._chat_params())
        assert AgentRegistry.size() == 0

    async def test_base_url_creates_per_call_agent(self, server: JsonRpcServer) -> None:
        """带 base_url → 走 AgentRegistry.reserve,registry size 变 1。"""
        await self._run_chat(server, self._chat_params(base_url="https://override.example/v1"))
        assert AgentRegistry.size() == 1

    async def test_api_key_creates_per_call_agent(self, server: JsonRpcServer) -> None:
        """单独 api_key 也触发 per-call agent。"""
        await self._run_chat(server, self._chat_params(api_key="sk-override-xyz"))
        assert AgentRegistry.size() == 1

    async def test_same_override_hits_lru_cache(self, server: JsonRpcServer) -> None:
        """同 (provider, base_url, api_key) 第二次调用命中缓存,size 不变。"""
        params = self._chat_params(base_url="https://override.example/v1")
        await self._run_chat(server, params)
        await self._run_chat(server, params)
        assert AgentRegistry.size() == 1

    async def test_different_override_creates_separate_agents(self, server: JsonRpcServer) -> None:
        """不同 base_url → 各自缓存,session_key hash 不撞。"""
        await self._run_chat(server, self._chat_params(base_url="https://override-a.example"))
        await self._run_chat(server, self._chat_params(base_url="https://override-b.example"))
        assert AgentRegistry.size() == 2

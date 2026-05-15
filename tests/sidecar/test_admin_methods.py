"""sidecar admin methods _________?0.6.5 S.8c)_?

_?`test_chat_method.py` ____________________?_______________?JsonRpcServer dispatch ___________________?,
_____________?AIAgent.bootstrap + tmp DB(_________?mock agent)_________?_________?admin methods
________________?session_maker / repos _____________?CRUD_?

________?14 _?method ______________?happy path + ___________________?

- list_conversations / get_conversation / rename_conversation / delete_conversation
- list_tools / enable_tool / disable_tool / config_tool
- list_providers / add_provider / update_provider / delete_provider / probe_provider
- list_logs

_____________________?NOT_FOUND / DUPLICATE / INVALID_PARAMS _?1-2 _?case_?
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
from chariot.rpc.jsonrpc import JsonRpcServer
from chariot.services.conversation import ConversationService
from chariot.services.log import LogService
from chariot.sidecar.methods import register_methods

# ---------------------------------------------------------------------------
# _____________________________?
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


async def _call(server: JsonRpcServer, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """_____________?RPC,_______________________________?response _?error)_?"""
    reader = make_reader(_request_frame(1, method, params))
    writer = MockWriter()
    await server.serve(reader, writer)
    return writer.lines()[0]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def _isolate() -> AsyncIterator[None]:
    """_?test _____________?AgentRegistry + DB engine,test ______________________?"""
    await AgentRegistry.clear()
    await dispose_db()
    yield
    await AgentRegistry.clear()
    await dispose_db()


@pytest_asyncio.fixture
async def agent(tmp_path: Path) -> AIAgent:
    """_?test ___________________?DB + _________?AIAgent;seed ___________________?mock entry + 4 disabled tool_?"""
    db_path = tmp_path / "chariot.db"
    return await AIAgent.bootstrap(db_path)


@pytest.fixture
def server(agent: AIAgent, tmp_path: Path) -> JsonRpcServer:
    """______________________?method _?JsonRpcServer _____________?

    `db_path` _?ChatMethod _?0.6.6+ per-call override _________?;admin methods
    _________________________________________,_?register_methods ____________________?_____________?agent fixture _____________?
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
        """_________?repo ___________________?2 _?conversation,_________?RPC list _____________?"""
        service = ConversationService(agent)
        await service.create("01H_TEST_A", title="first")
        await service.create("01H_TEST_B", title="second")

        line = await _call(server, "list_conversations")
        ids = sorted(c["id"] for c in line["result"]["conversations"])
        assert ids == ["01H_TEST_A", "01H_TEST_B"]

    async def test_get_conversation_returns_messages(self, server: JsonRpcServer, agent: AIAgent) -> None:
        service = ConversationService(agent)
        await service.create("01H_C", title="t")
        await service.append_user_message("01H_C", "hi")

        line = await _call(server, "get_conversation", {"conversation_id": "01H_C"})
        result = line["result"]
        assert result["conversation"]["id"] == "01H_C"
        assert len(result["messages"]) == 1
        assert result["messages"][0]["role"] == "user"

    async def test_get_conversation_not_found_returns_err_not_found(self, server: JsonRpcServer) -> None:
        line = await _call(server, "get_conversation", {"conversation_id": "ghost"})
        assert line["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND

    async def test_rename_conversation(self, server: JsonRpcServer, agent: AIAgent) -> None:
        await ConversationService(agent).create("01H_R", title="old")

        line = await _call(server, "rename_conversation", {"conversation_id": "01H_R", "title": "new"})
        assert line["result"]["conversation"]["title"] == "new"

    async def test_rename_conversation_not_found(self, server: JsonRpcServer) -> None:
        line = await _call(server, "rename_conversation", {"conversation_id": "ghost", "title": "x"})
        assert line["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND

    async def test_delete_conversation(self, server: JsonRpcServer, agent: AIAgent) -> None:
        await ConversationService(agent).create("01H_D", title="t")

        line = await _call(server, "delete_conversation", {"conversation_id": "01H_D"})
        assert line["result"] == {"deleted": "01H_D"}

        # _________?get _________?not found
        line2 = await _call(server, "get_conversation", {"conversation_id": "01H_D"})
        assert line2["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND

    async def test_conversation_id_required(self, server: JsonRpcServer) -> None:
        """params _?conversation_id _?ERR_INVALID_PARAMS_?"""
        line = await _call(server, "get_conversation", {})
        assert line["error"]["code"] == JsonRpcServer.ERR_INVALID_PARAMS


# ---------------------------------------------------------------------------
# tool methods
# ---------------------------------------------------------------------------


class TestToolMethods:
    async def test_list_tools_returns_seeded(self, server: JsonRpcServer) -> None:
        """sync_builtin_tools 落 12 条 fixture,全部 disabled。"""
        line = await _call(server, "list_tools")
        tools = line["result"]["tools"]
        assert len(tools) == 12
        names = sorted(t["name"] for t in tools)
        assert names == [
            "edit_file",
            "git_status",
            "http_get",
            "list_dir",
            "propose_skill",
            "read_file",
            "search_files",
            "shell_exec",
            "todo",
            "web_extract",
            "web_search",
            "write_file",
        ]
        # 全部 disabled
        assert all(not t["enabled"] for t in tools)

    async def test_enable_tool(self, server: JsonRpcServer) -> None:
        line = await _call(server, "enable_tool", {"name": "list_dir"})
        assert line["result"]["tool"]["enabled"] is True

    async def test_disable_tool(self, server: JsonRpcServer) -> None:
        await _call(server, "enable_tool", {"name": "list_dir"})
        line = await _call(server, "disable_tool", {"name": "list_dir"})
        assert line["result"]["tool"]["enabled"] is False

    async def test_config_tool_options(self, server: JsonRpcServer) -> None:
        line = await _call(server, "config_tool", {"name": "read_file", "options": {"max_bytes": 8192}})
        assert line["result"]["tool"]["options"] == {"max_bytes": 8192}

    async def test_enable_unknown_tool_returns_not_found(self, server: JsonRpcServer) -> None:
        line = await _call(server, "enable_tool", {"name": "no_such_tool"})
        assert line["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND


# ---------------------------------------------------------------------------
# provider methods
# ---------------------------------------------------------------------------


class TestProviderMethods:
    async def test_list_providers_returns_seeded_mock(self, server: JsonRpcServer) -> None:
        """seed_if_empty ___________________?mock entry_?"""
        line = await _call(server, "list_providers")
        providers = line["result"]["providers"]
        assert len(providers) == 1
        assert providers[0]["name"] == "Mock"
        assert providers[0]["slug"] == "mock"
        assert providers[0]["type"] == "mock"
        # seed_if_empty ______________________?mock ___________________?provider
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
        assert result["slug"] == "mock-mock2"
        assert result["type"] == "mock"

    async def test_add_provider_duplicate_name_gets_incremented_default_slug(self, server: JsonRpcServer) -> None:
        first = await _call(
            server,
            "add_provider",
            {"name": "Qwen", "type": "mock", "options": {}},
        )
        second = await _call(
            server,
            "add_provider",
            {"name": "Qwen", "type": "mock", "options": {}},
        )
        assert first["result"]["provider"]["slug"] == "mock-qwen"
        assert second["result"]["provider"]["slug"] == "mock-qwen-2"

    async def test_add_provider_duplicate_name_still_succeeds_with_new_slug(self, server: JsonRpcServer) -> None:
        """name 可重复;默认 slug 自动避让。"""
        line = await _call(
            server,
            "add_provider",
            {"name": "mock", "type": "mock", "options": {}},
        )
        assert line["result"]["provider"]["name"] == "mock"
        assert line["result"]["provider"]["slug"] == "mock-mock"

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
        """seeded mock entry _?probe _?ok=True(MockProvider _________?HTTP)_?"""
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
        service = LogService(agent)
        await service.create(provider="mock", status="ok", latency_ms=42)
        await service.create(provider="mock", status="error", error="boom")

        line = await _call(server, "list_logs")
        logs = line["result"]["logs"]
        assert len(logs) == 2
        statuses = sorted(log["status"] for log in logs)
        assert statuses == ["error", "ok"]

    async def test_list_logs_limit(self, server: JsonRpcServer, agent: AIAgent) -> None:
        service = LogService(agent)
        for _ in range(5):
            await service.create(provider="mock", status="ok")

        line = await _call(server, "list_logs", {"limit": 2})
        assert len(line["result"]["logs"]) == 2

    async def test_list_logs_invalid_limit_returns_err_params(self, server: JsonRpcServer) -> None:
        line = await _call(server, "list_logs", {"limit": -1})
        assert line["error"]["code"] == JsonRpcServer.ERR_INVALID_PARAMS

    async def test_list_logs_since_filter(self, server: JsonRpcServer, agent: AIAgent) -> None:
        """since _________?_?_________?created_at > since ______________?"""
        await LogService(agent).create(provider="mock", status="ok")

        # __________________________________________________?created_at _?since,_____________?
        future_str = "2099-01-01T00:00:00"
        line = await _call(server, "list_logs", {"since": future_str})
        assert line["result"]["logs"] == []

    async def test_list_logs_invalid_iso(self, server: JsonRpcServer) -> None:
        line = await _call(server, "list_logs", {"since": "not a date"})
        assert line["error"]["code"] == JsonRpcServer.ERR_INVALID_PARAMS


# ---------------------------------------------------------------------------
# _?method:method not found / _____________________?method _?
# ---------------------------------------------------------------------------


class TestRegistration:
    async def test_unknown_method_returns_method_not_found(self, server: JsonRpcServer) -> None:
        line = await _call(server, "no_such_method")
        assert line["error"]["code"] == JsonRpcServer.ERR_METHOD_NOT_FOUND

    def test_register_methods_core_present(self, server: JsonRpcServer) -> None:
        """register_methods exposes at least the 0.6.5-era core methods."""
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
            "show_provider",
            "add_provider",
            "update_provider",
            "delete_provider",
            "use_provider",
            "probe_provider",
            "get_provider_status",
            "list_prompt_bundles",
            "get_prompt_bundle",
            "list_prompt_versions",
            "get_prompt_version",
            "list_prompt_traces",
            "inspect_prompt",
            "list_memories",
            "get_memory",
            "create_memory",
            "update_memory",
            "delete_memory",
            "pin_memory",
            "archive_memory",
            "list_memory_events",
            "list_memory_links",
            "search_memory",
            "list_logs",
        }
        assert expected.issubset(server.known_methods())


# ---------------------------------------------------------------------------
# Chat per-call override(0.6.6+)
#
# _____________?Chat _____________?CLI _?`--base-url` / `--api-key`:RPC params ___________________?
# ___________________?___________________?_?ChatMethod _?AgentRegistry.reserve(session_key _?
# (provider_name, sorted options) _?sha _________?_?per-call AIAgent_?
# _?override _?____________?agent(fixture _________?bootstrap,_________?registry)_?
#
# ____________________?`AgentRegistry.size()` _________?default agent _________?registry _?________?size
# ____________________"per-call agent ____________________________?_?
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
        """_____________?chat,_?response _?_____________?N _?chat_event notify,response ______________?_?"""
        reader = make_reader(_request_frame(1, "chat", params))
        writer = MockWriter()
        await server.serve(reader, writer)
        responses = [line for line in writer.lines() if "result" in line]
        assert len(responses) == 1, f"expected 1 response, got {len(responses)}"
        return responses[0]

    async def test_no_override_skips_registry(self, server: JsonRpcServer) -> None:
        """_?base_url / api_key _?ChatMethod _?default agent,_________?AgentRegistry_?"""
        await self._run_chat(server, self._chat_params())
        assert AgentRegistry.size() == 0

    async def test_base_url_creates_per_call_agent(self, server: JsonRpcServer) -> None:
        """_?base_url _?_?AgentRegistry.reserve,registry size _?1_?"""
        await self._run_chat(server, self._chat_params(base_url="https://override.example/v1"))
        assert AgentRegistry.size() == 1

    async def test_api_key_creates_per_call_agent(self, server: JsonRpcServer) -> None:
        """_________?api_key __________?per-call agent_?"""
        await self._run_chat(server, self._chat_params(api_key="sk-override-xyz"))
        assert AgentRegistry.size() == 1

    async def test_same_override_hits_lru_cache(self, server: JsonRpcServer) -> None:
        """_?(provider, base_url, api_key) __________________________________________?size _____________?"""
        params = self._chat_params(base_url="https://override.example/v1")
        await self._run_chat(server, params)
        await self._run_chat(server, params)
        assert AgentRegistry.size() == 1

    async def test_different_override_creates_separate_agents(self, server: JsonRpcServer) -> None:
        """_________?base_url _?____________________?session_key hash _____________?"""
        await self._run_chat(server, self._chat_params(base_url="https://override-a.example"))
        await self._run_chat(server, self._chat_params(base_url="https://override-b.example"))
        assert AgentRegistry.size() == 2

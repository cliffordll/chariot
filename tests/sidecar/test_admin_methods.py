"""sidecar admin methods 闂佸憡顨嗗ú妯肩矈?0.6.5 S.8c)闂?

闁?`test_chat_method.py` 閻熸粏鍩囬崹娲焵椤戞寧顦风紒鏃€鎸抽幊?闂備緡鍠曠划娆撳焵椤掍椒浜㈢紒?JsonRpcServer dispatch 闁荤姍鍕棆妞ゅ浚鍓熷畷姘跺箥椤曞懍绱?,
婵炶揪绲藉Λ娑㈠极閵堝鍎?AIAgent.bootstrap + tmp DB(闂佸吋婢橀惌鍌氼焽?mock agent)闂佺偨鍎查弻锟犲焵?闂佹悶鍎虫慨宕囨嫻?admin methods
闂傚倸娲犻崑鎾绘偡閺囨俺鍏屾繝鈧?session_maker / repos 闂佸綊娼х粔鐑藉礂濡吋宕?CRUD闂?

闁荤喐娲栧Λ娑樏?14 婵?method 闂佸憡鑹剧€氼垶宕靛鍫熷剭?happy path + 闂佺绻戞繛濠囧极椤撶喐缍囬柛锔诲幗濞?

- list_conversations / get_conversation / rename_conversation / delete_conversation
- list_tools / enable_tool / disable_tool / config_tool
- list_providers / add_provider / update_provider / delete_provider / probe_provider
- list_logs

闂備焦瀵ч悷銊╊敋閵堝鍎樺ù锝堫潐琛奸柣?NOT_FOUND / DUPLICATE / INVALID_PARAMS 闂?1-2 婵?case闂?
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
# 濠电偞娼欓鍫ユ儊椤栫偛鏄ラ柧蹇氼嚃閺€銊╂偣娴ｅ搫浜鹃柡?
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
    """闁荤姷鍎ら崹宕囩博閺夋垟鏋?RPC,闁哄鏅滈弻銊ッ洪弽顐ょ當妞ゆ垼娉曢閬嶆偨椤栧棗鏌婇幒妤€鍑?response 闂?error)闂?""
    reader = make_reader(_request_frame(1, method, params))
    writer = MockWriter()
    await server.serve(reader, writer)
    return writer.lines()[0]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def _isolate() -> AsyncIterator[None]:
    """濠?test 闂佸憡鎸哥粔鎾箖濡も偓閵?AgentRegistry + DB engine,test 闂傚倸鍊搁悺銊ノ涢妶鍡欌枖闁圭粯甯掗柊閬嶆煏?""
    await AgentRegistry.clear()
    await dispose_db()
    yield
    await AgentRegistry.clear()
    await dispose_db()


@pytest_asyncio.fixture
async def agent(tmp_path: Path) -> AIAgent:
    """濠?test 婵炴垶鎸撮崑鎾斥槈閹垮啩绨婚柛娆忕箻瀵?DB + 闂佺绻堥崝宥夊蓟?AIAgent;seed 闁荤姷鍎ら崹鐢告偩椤掑嫬瑙﹂幖绮瑰墲閸?mock entry + 4 disabled tool闂?""
    db_path = tmp_path / "chariot.db"
    return await AIAgent.bootstrap(db_path)


@pytest.fixture
def server(agent: AIAgent, tmp_path: Path) -> JsonRpcServer:
    """閻庡湱顭堝鍫曞极閻愬搫绀冮悘鐐舵瀵灝鈹?method 闂?JsonRpcServer 闁诲骸婀遍崑妯兼閵夆晛违?

    `db_path` 缂?ChatMethod 闂?0.6.6+ per-call override 闁荤姳璀﹂崹鎵?;admin methods
    闁荤姷鍎ら崹鐟扳枔閹寸偟鈻旂€广儱鐗愬▔鏌ユ⒑椤撱劎绋绘繛纰卞灣閹瑰嫰顢涘杈╃嵁,婵?register_methods 缂備焦绋掗崕鎶藉箖閺囩姵鍟哄ù锝呮贡濠€?闁哄鏅滈悷鈺呭闯妞嬪孩宕?agent fixture 闂佸憡鑹鹃張顒傝姳椤曗偓婵?
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
        """闂佺绻愰悧蹇涘极?repo 闂佺儵鏅涢悺銊ф暜閹绢喖绠甸柟鐑樺灥瀵?2 婵?conversation,闂佸憡鍔曠粔鐑芥憘?RPC list 婵°倗濮撮惌渚€鎯佹径鎰?""
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

        # 闂佸憡鍔曠粔鐢割敃?get 闁圭厧鐡ㄥΛ渚€顢?not found
        line2 = await _call(server, "get_conversation", {"conversation_id": "01H_D"})
        assert line2["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND

    async def test_conversation_id_required(self, server: JsonRpcServer) -> None:
        """params 缂?conversation_id 闂?ERR_INVALID_PARAMS闂?""
        line = await _call(server, "get_conversation", {})
        assert line["error"]["code"] == JsonRpcServer.ERR_INVALID_PARAMS


# ---------------------------------------------------------------------------
# tool methods
# ---------------------------------------------------------------------------


class TestToolMethods:
    async def test_list_tools_returns_4_seeded(self, server: JsonRpcServer) -> None:
        """seed_if_empty 闁?4 闂?fixture(read_file / list_dir / shell_exec / http_get),
        闂?disabled闂?""
        line = await _call(server, "list_tools")
        tools = line["result"]["tools"]
        assert len(tools) == 4
        names = sorted(t["name"] for t in tools)
        assert names == ["http_get", "list_dir", "read_file", "shell_exec"]
        # 婵帗绋掗…鍫ヮ敇婵犳艾绀?disabled
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
        """seed_if_empty 闂佸湱绮敮鎺楀矗閸℃鈻旈柍褜鍓氱粙?mock entry闂?""
        line = await _call(server, "list_providers")
        providers = line["result"]["providers"]
        assert len(providers) == 1
        assert providers[0]["name"] == "mock"
        assert providers[0]["type"] == "mock"
        # seed_if_empty 婵☆偓绲鹃悧鏇㈠箚鎼淬劍鍎庨悗娑櫭径宥夋煙?mock 闁荤姳鑳堕崑鎾诲垂濮橆収娓舵俊顖涱儥閸?provider
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
        """seed 閻庤鐡曠亸娆戝垝閿熺姴瀚?'mock',闂佸憡鍔曠粔瀛樻叏閻愬搫瑙﹂悘鐐跺Г閸?闂?ERR_DUPLICATE闂?""
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
        """seeded mock entry 闁?probe 闂?ok=True(MockProvider 婵炴垶鎸哥粔纾嬨亹?HTTP)闂?""
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
        """since 闁哄鏅涘ú锕傚箮?闂?闂佸憡鐟禍锝囨崲?created_at > since 闂佹眹鍔岀€氼垶銆侀幋锕€违?""
        async with agent.session_maker() as session:
            repo = LogRepo(session)
            await repo.create(provider="mock", status="ok")

        # 闂佸憡鐟﹂悧鏃傜博鐎涙鈻旀い蹇撴搐娴犳劙鎮楃憴鍕闁靛洤娲︾粋宥嗘償閿濆懐鐤€闂佸搫鐗嗛ˇ鎶姐€?created_at 闂?since,闁圭厧鐡ㄥΛ浣烘崲閹寸姷鐭?
        future_str = "2099-01-01T00:00:00"
        line = await _call(server, "list_logs", {"since": future_str})
        assert line["result"]["logs"] == []

    async def test_list_logs_invalid_iso(self, server: JsonRpcServer) -> None:
        line = await _call(server, "list_logs", {"since": "not a date"})
        assert line["error"]["code"] == JsonRpcServer.ERR_INVALID_PARAMS


# ---------------------------------------------------------------------------
# 闁?method:method not found / 闂佸搫鐗滄禍婵嬪极閻愬搫绀冪€光偓閳ь剙鈻?method 闂?
# ---------------------------------------------------------------------------


class TestRegistration:
    async def test_unknown_method_returns_method_not_found(self, server: JsonRpcServer) -> None:
        line = await _call(server, "no_such_method")
        assert line["error"]["code"] == JsonRpcServer.ERR_METHOD_NOT_FOUND

    def test_register_methods_all_21_present(self, server: JsonRpcServer) -> None:
        """register_methods 闁圭厧鐡ㄥΛ渚€顢氬顑芥灃闁靛鍎遍弬鈧?21 婵?method 闂佸憡鑹剧粔鏌ュ焵?""
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
        assert server.known_methods() == expected


# ---------------------------------------------------------------------------
# Chat per-call override(0.6.6+)
#
# 濠碘剝顨呴惌鍌氼焽閹殿喚鍗?Chat 婵＄偑鍊濆褔顢氶绛嬬€?CLI 闂?`--base-url` / `--api-key`:RPC params 婵犮垼鍩栫喊宥囨暜鐎涙鈻旈柕鍫濆閸?
# 闂佸憡鐟崹鍫曞焵椤掆偓椤︻垶鎮鸿閳?婵炲濮鹃濠勭博閹绢喗顥堥柣鎰暯閺?闂?ChatMethod 闁?AgentRegistry.reserve(session_key 闂?
# (provider_name, sorted options) 闂?sha 缂備胶濮甸〃鍛村吹?闂?per-call AIAgent闂?
# 闂?override 闂?闁荤姍鍌氬祮缂侇噮鍨抽幏?agent(fixture 闂佺儵鏅涢悺銊ф暜?bootstrap,婵炴垶鎸哥粔椋庢崲?registry)闂?
#
# 婵°倗濮撮惌渚€鎯佹径鎰闁圭偓娼欓·?`AgentRegistry.size()` 闂佺偨鍎查弻锟犲焵?default agent 婵炴垶鎸哥粔鏉戯耿?registry 闂?闂佸湱顣介崑鎾趁?size
# 闁诲海鎳撻張顒勫汲閿濆鐭楃€广儱妫欒〖"per-call agent 缂傚倸鍊归幐鎼佹偤閵娾晛瀚夊璺侯煬濡鎮樿箛鏂跨仸婵?闂?
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
        """闁荤姷鍎ら崹宕囩博閺夋垟鏋?chat,闂?response 闁?婵炴垶鎼╅崣鍐ㄎ涢崸妤€瀚?N 婵?chat_event notify,response 闂侀潻璐熼崝宥呫€掗崼鏇炶Е?闂?""
        reader = make_reader(_request_frame(1, "chat", params))
        writer = MockWriter()
        await server.serve(reader, writer)
        responses = [line for line in writer.lines() if "result" in line]
        assert len(responses) == 1, f"expected 1 response, got {len(responses)}"
        return responses[0]

    async def test_no_override_skips_registry(self, server: JsonRpcServer) -> None:
        """闂?base_url / api_key 闂?ChatMethod 闁?default agent,婵炴垶鎸哥粔瀛樻叏?AgentRegistry闂?""
        await self._run_chat(server, self._chat_params())
        assert AgentRegistry.size() == 0

    async def test_base_url_creates_per_call_agent(self, server: JsonRpcServer) -> None:
        """闁?base_url 闂?闁?AgentRegistry.reserve,registry size 闂?1闂?""
        await self._run_chat(server, self._chat_params(base_url="https://override.example/v1"))
        assert AgentRegistry.size() == 1

    async def test_api_key_creates_per_call_agent(self, server: JsonRpcServer) -> None:
        """闂佸憡顨嗗ú婊呪偓?api_key 婵炴垶姊婚崰鏇☆杺闂?per-call agent闂?""
        await self._run_chat(server, self._chat_params(api_key="sk-override-xyz"))
        assert AgentRegistry.size() == 1

    async def test_same_override_hits_lru_cache(self, server: JsonRpcServer) -> None:
        """闂?(provider, base_url, api_key) 缂備焦顨忛崗娑氳姳閳轰讲鏋庨梽鍥儍閻斿吋鍋ㄩ柕濞垮劜閸ゆ帒鈽夐幙鍐ㄥ箻缂佹唻濡囬埀?size 婵炴垶鎸哥粔纾嬨亹婢舵劕违?""
        params = self._chat_params(base_url="https://override.example/v1")
        await self._run_chat(server, params)
        await self._run_chat(server, params)
        assert AgentRegistry.size() == 1

    async def test_different_override_creates_separate_agents(self, server: JsonRpcServer) -> None:
        """婵炴垶鎸哥粔鎾箖?base_url 闂?闂佸憡鑹剧€氼垶宕靛鍛＝闁规儳纾幗?session_key hash 婵炴垶鎸哥粔鐢稿箰閹烘违?""
        await self._run_chat(server, self._chat_params(base_url="https://override-a.example"))
        await self._run_chat(server, self._chat_params(base_url="https://override-b.example"))
        assert AgentRegistry.size() == 2

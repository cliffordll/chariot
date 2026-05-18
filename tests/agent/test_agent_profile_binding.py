"""phase 4 binding tests:agent_profile -> provider_id / prompt_id / toolset_id。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest_asyncio

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest, Message
from chariot.agent.run import AIAgent
from chariot.database.session import dispose_db, init_db
from chariot.providers.base import BaseProvider, BaseProviderConfig
from chariot.repos.prompt_repo import PromptRepo
from chariot.repos.task_repo import TaskRepo
from chariot.repos.toolset_repo import ToolsetRepo
from chariot.tools.base import BaseTool


class _CapturingProvider(BaseProvider):
    """记最近一次 generate 收到的 req,用来 assert 路由 / system / tools。"""

    def __init__(self, name: str = "mock") -> None:
        self.config = BaseProviderConfig(name=name, model="mock-1")
        self.last_req: ChatRequest | None = None

    @classmethod
    def create(cls, options: dict[str, Any]) -> _CapturingProvider:
        return cls()

    async def generate(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        self.last_req = req
        yield ChatEvent.message_start(message_id="m", model=self.config.model)
        yield ChatEvent.text_block_start(index=0)
        yield ChatEvent.text_delta("ok", index=0)
        yield ChatEvent.block_stop(index=0)
        yield ChatEvent.message_delta_done(stop_reason="end_turn")
        yield ChatEvent.message_done()


class _StubTool(BaseTool):
    """schema 固定,execute 不调用(本测试只关心 tools 注入列表)。"""

    def __init__(self, name: str) -> None:
        self.name = name

    @classmethod
    def create(cls, entry: Any) -> _StubTool:
        return cls(entry.name)

    def schema(self) -> dict[str, Any]:
        return {"name": self.name, "description": "", "input_schema": {"type": "object", "properties": {}}}

    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        del input
        return {"type": "tool_result", "content": [{"type": "text", "text": "ok"}]}


def _make_tool(name: str) -> BaseTool:
    return _StubTool(name)


@pytest_asyncio.fixture
async def sessionmaker(tmp_path: Path):
    db_path = tmp_path / "chariot.db"
    sm = await init_db(db_path)
    try:
        yield sm
    finally:
        await dispose_db()


def _make_agent(sessionmaker, *, providers: dict[str, BaseProvider], tools: dict[str, BaseTool]) -> AIAgent:
    return AIAgent(providers=providers, tools=tools, sessionmaker=sessionmaker)


def _stateless(provider_ref: str, *, agent_profile: str | None = None) -> ChatRequest:
    return ChatRequest(
        provider_ref=provider_ref,
        messages=[Message(role="user", content="hi")],
        agent_profile=agent_profile,
    )


class TestAgentProfileBinding:
    async def test_no_binding_fallback(self, sessionmaker) -> None:
        provider = _CapturingProvider("mock")
        agent = _make_agent(sessionmaker, providers={"mock": provider}, tools={})
        events = [e async for e in agent.run_chat(_stateless("mock"))]
        assert any(e.kind == "stream_done" for e in events)
        assert provider.last_req is not None
        # 没绑定 → provider 路由原样,tools=None(没装载 tool 时不挂)
        assert provider.last_req.provider_ref == "mock"

    async def test_dangling_agent_profile_fallback(self, sessionmaker) -> None:
        provider = _CapturingProvider("mock")
        agent = _make_agent(sessionmaker, providers={"mock": provider}, tools={})
        events = [e async for e in agent.run_chat(_stateless("mock", agent_profile="ghost"))]
        assert any(e.kind == "stream_done" for e in events)
        # dangling profile name → 不阻断,走原 provider
        assert provider.last_req is not None
        assert provider.last_req.provider_ref == "mock"

    async def test_provider_id_overrides_provider_ref(self, sessionmaker) -> None:
        primary = _CapturingProvider("primary")
        secondary = _CapturingProvider("secondary")
        agent = _make_agent(sessionmaker, providers={"primary": primary, "secondary": secondary}, tools={})
        async with sessionmaker() as session:
            await TaskRepo(session).create_agent_profile(
                name="researcher",
                role="research",
                provider_id="secondary",
            )
        events = [e async for e in agent.run_chat(_stateless("primary", agent_profile="researcher"))]
        assert any(e.kind == "stream_done" for e in events)
        # provider 被切到 secondary
        assert secondary.last_req is not None
        assert primary.last_req is None

    async def test_prompt_id_pinned_overrides_active(self, sessionmaker) -> None:
        provider = _CapturingProvider("mock")
        agent = _make_agent(sessionmaker, providers={"mock": provider}, tools={})
        async with sessionmaker() as session:
            repo = PromptRepo(session)
            # 先建 research(创建后变 active),再建 override(创建后变 active,
            # research 不再 active)。这样能区分"pinned bundle" vs "active bundle"
            await repo.create_bundle(
                "research",
                layers=[{"name": "base_system", "source": "research", "content": "RESEARCH MARKER"}],
            )
            await repo.create_bundle(
                "override",
                layers=[{"name": "base_system", "source": "override", "content": "OVERRIDE MARKER"}],
            )
            pinned = await repo.get_bundle("research")
            assert pinned is not None
            await TaskRepo(session).create_agent_profile(
                name="researcher",
                role="research",
                prompt_id=pinned.id,
            )
        events = [e async for e in agent.run_chat(_stateless("mock", agent_profile="researcher"))]
        assert any(e.kind == "stream_done" for e in events)
        assert provider.last_req is not None
        system = provider.last_req.system
        assert isinstance(system, str)
        # pinned bundle 命中(RESEARCH),不是 active 的 OVERRIDE
        assert "RESEARCH MARKER" in system
        assert "OVERRIDE MARKER" not in system

    async def test_prompt_id_dangling_falls_back_to_active(self, sessionmaker) -> None:
        provider = _CapturingProvider("mock")
        agent = _make_agent(sessionmaker, providers={"mock": provider}, tools={})
        async with sessionmaker() as session:
            await PromptRepo(session).create_bundle(
                "default_active",
                layers=[{"name": "base_system", "source": "fallback", "content": "ACTIVE FALLBACK"}],
            )
            await TaskRepo(session).create_agent_profile(
                name="bad_prompt",
                role="x",
                prompt_id="ghost_bundle",
            )
        events = [e async for e in agent.run_chat(_stateless("mock", agent_profile="bad_prompt"))]
        assert any(e.kind == "stream_done" for e in events)
        assert provider.last_req is not None
        system = provider.last_req.system
        assert isinstance(system, str)
        # dangling bundle 名 → 回退 active bundle
        assert "ACTIVE FALLBACK" in system

    async def test_toolset_filter_limits_tools(self, sessionmaker) -> None:
        provider = _CapturingProvider("mock")
        tools = {
            "read_file": _make_tool("read_file"),
            "list_dir": _make_tool("list_dir"),
            "shell_exec": _make_tool("shell_exec"),
        }
        agent = _make_agent(sessionmaker, providers={"mock": provider}, tools=tools)
        async with sessionmaker() as session:
            await ToolsetRepo(session).create(name="fs_safe", members=["read_file", "list_dir"])
            toolset = await ToolsetRepo(session).get_entry("fs_safe")
            assert toolset is not None
            await TaskRepo(session).create_agent_profile(
                name="safe",
                role="reader",
                toolset_id=toolset.id,
            )
        events = [e async for e in agent.run_chat(_stateless("mock", agent_profile="safe"))]
        assert any(e.kind == "stream_done" for e in events)
        assert provider.last_req is not None
        tool_names = {t.name for t in (provider.last_req.tools or [])}
        assert tool_names == {"read_file", "list_dir"}

    async def test_toolset_empty_disables_tools(self, sessionmaker) -> None:
        provider = _CapturingProvider("mock")
        tools = {"read_file": _make_tool("read_file"), "list_dir": _make_tool("list_dir")}
        agent = _make_agent(sessionmaker, providers={"mock": provider}, tools=tools)
        async with sessionmaker() as session:
            await ToolsetRepo(session).create(name="none")
            toolset = await ToolsetRepo(session).get_entry("none")
            assert toolset is not None
            await TaskRepo(session).create_agent_profile(
                name="tooless",
                role="x",
                toolset_id=toolset.id,
            )
        events = [e async for e in agent.run_chat(_stateless("mock", agent_profile="tooless"))]
        assert any(e.kind == "stream_done" for e in events)
        assert provider.last_req is not None
        assert provider.last_req.tools == []

    async def test_dangling_toolset_name_fallback(self, sessionmaker) -> None:
        provider = _CapturingProvider("mock")
        tools = {"read_file": _make_tool("read_file"), "list_dir": _make_tool("list_dir")}
        agent = _make_agent(sessionmaker, providers={"mock": provider}, tools=tools)
        async with sessionmaker() as session:
            await TaskRepo(session).create_agent_profile(
                name="bad",
                role="x",
                toolset_id="ghost_toolset",
            )
        events = [e async for e in agent.run_chat(_stateless("mock", agent_profile="bad"))]
        assert any(e.kind == "stream_done" for e in events)
        # dangling toolset → 0.8.8+ 回退到空工具(不挂载任何工具)
        assert provider.last_req is not None
        assert provider.last_req.tools == []

    async def test_no_agent_profile_no_tools(self, sessionmaker) -> None:
        """没有 agent_profile → 不挂载任何工具(0.8.8+ 行为变更)。"""
        provider = _CapturingProvider("mock")
        tools = {"read_file": _make_tool("read_file"), "list_dir": _make_tool("list_dir")}
        agent = _make_agent(sessionmaker, providers={"mock": provider}, tools=tools)
        events = [e async for e in agent.run_chat(_stateless("mock"))]
        assert any(e.kind == "stream_done" for e in events)
        assert provider.last_req is not None
        assert provider.last_req.tools == []

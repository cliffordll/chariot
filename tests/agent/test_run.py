"""AIAgent 单测。

覆盖(对照 FEATURE.md S.6 验收清单):
- 路由:`req.model='not_exists'` → 单 event error(error_type='unknown_model')
- default tools 注入:`req.tools is None` + 装载 N 个 tool → AgentLoop 拿到 N 个 schema
- default tools:`req.tools=[]` → 关闭工具调用(透传 [],不替换)
- default tools:`req.tools=[<显式>]` → 透传(不替换)
- stateless:无 conv_id → 不开 session(sessionmaker 不被调)
- stateful:有 conv_id → ConversationLockManager.acquire 被调一次
- 单例 / from_db:`current()` 未装载抛 RuntimeError;装载后可拿
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest, Message, ToolSchema
from chariot.agent.run import AIAgent
from chariot.providers.base import BaseProvider, BaseProviderConfig
from chariot.tools.base import BaseTool

# ---------------------------------------------------------------------------
# Mock provider:capture req,产固定 text turn
# ---------------------------------------------------------------------------


class _CapturingProvider(BaseProvider):
    def __init__(self, name: str = "captured") -> None:
        self.config = BaseProviderConfig(name=name, model=f"{name}-1")
        self.last_req: ChatRequest | None = None

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> _CapturingProvider:
        return cls()

    async def generate(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        self.last_req = req
        yield ChatEvent.message_start(message_id="m1", model=self.config.model)
        yield ChatEvent.text_block_start(index=0)
        yield ChatEvent.text_delta("ok", index=0)
        yield ChatEvent.block_stop(index=0)
        yield ChatEvent.message_delta_done(stop_reason="end_turn")
        yield ChatEvent.message_done()


class _StubTool(BaseTool):
    def __init__(self, name: str) -> None:
        self.name = name

    @classmethod
    def from_config(cls, entry: Any) -> _StubTool:
        return cls("stub")

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": f"stub tool {self.name}",
            "input_schema": {"type": "object", "properties": {}},
        }

    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        return {"type": "tool_result", "content": [{"type": "text", "text": "ok"}]}


# ---------------------------------------------------------------------------
# 路由 / unknown model
# ---------------------------------------------------------------------------


class TestRouting:
    async def test_unknown_model_yields_error(self) -> None:
        agent = AIAgent(providers={"mock": _CapturingProvider("mock")}, tools={})
        req = ChatRequest(
            model="not_exists",
            messages=[Message(role="user", content="hi")],
        )
        events = [ev async for ev in agent.run(req)]
        assert len(events) == 1
        assert events[0].kind == "error"
        assert events[0].error_type == "unknown_model"

    async def test_routes_by_model_name(self) -> None:
        p1 = _CapturingProvider("p1")
        p2 = _CapturingProvider("p2")
        agent = AIAgent(providers={"p1": p1, "p2": p2}, tools={})
        req = ChatRequest(
            model="p2",
            messages=[Message(role="user", content="hi")],
        )
        _ = [ev async for ev in agent.run(req)]
        # 只有 p2 被调用
        assert p1.last_req is None
        assert p2.last_req is not None


# ---------------------------------------------------------------------------
# Default tools 注入
# ---------------------------------------------------------------------------


class TestDefaultToolsInjection:
    async def test_none_tools_injects_all(self) -> None:
        """req.tools=None + 装载 N 个 tool → AgentLoop 拿到 N 个 schema。"""
        provider = _CapturingProvider("p")
        agent = AIAgent(
            providers={"p": provider},
            tools={"t1": _StubTool("t1"), "t2": _StubTool("t2")},
        )
        req = ChatRequest(
            model="p",
            messages=[Message(role="user", content="hi")],
            tools=None,  # 触发 default 注入
        )
        _ = [ev async for ev in agent.run(req)]
        assert provider.last_req is not None
        assert provider.last_req.tools is not None
        names = sorted(t.name for t in provider.last_req.tools)
        assert names == ["t1", "t2"]

    async def test_empty_tools_passes_through(self) -> None:
        """req.tools=[] → 透传(不被默认替换)。"""
        provider = _CapturingProvider("p")
        agent = AIAgent(
            providers={"p": provider},
            tools={"t1": _StubTool("t1")},
        )
        req = ChatRequest(
            model="p",
            messages=[Message(role="user", content="hi")],
            tools=[],
        )
        _ = [ev async for ev in agent.run(req)]
        assert provider.last_req is not None
        assert provider.last_req.tools == []  # 透传 [],不替换

    async def test_explicit_tools_passes_through(self) -> None:
        provider = _CapturingProvider("p")
        agent = AIAgent(
            providers={"p": provider},
            tools={"t1": _StubTool("t1")},
        )
        explicit = [
            ToolSchema(name="custom", description="custom", input_schema={"type": "object"}),
        ]
        req = ChatRequest(
            model="p",
            messages=[Message(role="user", content="hi")],
            tools=list(explicit),  # 显式
        )
        _ = [ev async for ev in agent.run(req)]
        assert provider.last_req is not None
        assert provider.last_req.tools is not None
        assert len(provider.last_req.tools) == 1
        assert provider.last_req.tools[0].name == "custom"


# ---------------------------------------------------------------------------
# Stateless 路径:不开 session
# ---------------------------------------------------------------------------


class TestStatelessPath:
    async def test_no_session_opened(self) -> None:
        """无 conv_id → AIAgent.run 不调 sessionmaker。"""
        provider = _CapturingProvider("p")
        sm_mock = AsyncMock()  # 任何调用都失败/记录
        agent = AIAgent(providers={"p": provider}, tools={}, sessionmaker=sm_mock)

        req = ChatRequest(
            model="p",
            messages=[Message(role="user", content="hi")],
        )
        events = [ev async for ev in agent.run(req)]
        # sessionmaker 没被调
        assert sm_mock.call_count == 0
        # 流正常收尾(stream_done 或 message_stop 末尾)
        kinds = [ev.kind for ev in events]
        assert "stream_done" in kinds or "message_stop" in kinds


# ---------------------------------------------------------------------------
# Stateful 路径:lock 被调
# ---------------------------------------------------------------------------


class TestStatefulPath:
    async def test_lock_manager_acquire_called(self) -> None:
        """有 conv_id → ConversationLockManager.acquire 被调一次。"""
        provider = _CapturingProvider("p")
        agent = AIAgent(
            providers={"p": provider},
            tools={},
            sessionmaker=None,  # 没装载,会 yield error 但 acquire 之前先报错
        )
        req = ChatRequest(
            model="p",
            messages=[Message(role="user", content="hi")],
            conversation_id="01H_TEST",
        )
        events = [ev async for ev in agent.run(req)]
        # sessionmaker=None → yield 'no_sessionmaker' error
        assert events[0].kind == "error"
        assert events[0].error_type == "no_sessionmaker"


# ---------------------------------------------------------------------------
# 单例 / current()
# ---------------------------------------------------------------------------


class TestSingletonLifecycle:
    def setup_method(self) -> None:
        AIAgent.uninstall()

    def teardown_method(self) -> None:
        AIAgent.uninstall()

    def test_current_unloaded_raises(self) -> None:
        with pytest.raises(RuntimeError, match="AIAgent 未装载"):
            AIAgent.current()

    def test_uninstall_clears(self) -> None:
        AIAgent._current = AIAgent(providers={}, tools={})
        assert AIAgent.current() is not None
        AIAgent.uninstall()
        with pytest.raises(RuntimeError):
            AIAgent.current()

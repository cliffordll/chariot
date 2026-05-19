"""B1 phase 5 集成测试:AIAgent / AgentLoop 真实跑过后,trace 应该有完整记录。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, ClassVar

import pytest_asyncio

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest, Message
from chariot.agent.run import AIAgent
from chariot.database.session import dispose_db, init_db
from chariot.models.trace import ToolCallStatus, TurnStatus
from chariot.providers.base import BaseProvider, BaseProviderConfig
from chariot.repos.trace_repo import TraceRepo
from chariot.tools.base import BaseTool


class _SimpleProvider(BaseProvider):
    """普通 provider:一轮 text 输出 + message_delta_done。"""

    def __init__(self) -> None:
        self.config = BaseProviderConfig(name="mock", model="mock-1")

    @classmethod
    def create(cls, options: dict[str, Any]) -> _SimpleProvider:
        return cls()

    async def generate(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        yield ChatEvent.message_start(
            message_id="m1",
            model=self.config.model,
            usage={"input_tokens": 12, "output_tokens": 0},
        )
        yield ChatEvent.text_block_start(index=0)
        yield ChatEvent.text_delta("hi", index=0)
        yield ChatEvent.block_stop(index=0)
        yield ChatEvent.message_delta_done(
            stop_reason="end_turn",
            usage={"input_tokens": 12, "output_tokens": 5},
        )
        yield ChatEvent.message_done()


class _ToolUsingProvider(BaseProvider):
    """第一轮返 tool_use,第二轮返普通 text。简化的工具循环 driver。"""

    def __init__(self) -> None:
        self.config = BaseProviderConfig(name="mock", model="mock-1")
        self.call_count = 0

    @classmethod
    def create(cls, options: dict[str, Any]) -> _ToolUsingProvider:
        return cls()

    async def generate(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        self.call_count += 1
        yield ChatEvent.message_start(message_id=f"m{self.call_count}", model=self.config.model)
        if self.call_count == 1:
            yield ChatEvent.tool_use_block_start(
                index=0,
                tool_use_id="toolu_1",
                tool_name="echo_tool",
            )
            yield ChatEvent.input_json_delta('{"text":"hello"}', index=0)
            yield ChatEvent.block_stop(index=0)
            yield ChatEvent.message_delta_done(stop_reason="tool_use")
        else:
            yield ChatEvent.text_block_start(index=0)
            yield ChatEvent.text_delta("done", index=0)
            yield ChatEvent.block_stop(index=0)
            yield ChatEvent.message_delta_done(
                stop_reason="end_turn",
                usage={"input_tokens": 20, "output_tokens": 10},
            )
        yield ChatEvent.message_done()


class _ErrorProvider(BaseProvider):
    """模拟 provider 内 yield error event。"""

    def __init__(self) -> None:
        self.config = BaseProviderConfig(name="mock", model="mock-1")

    @classmethod
    def create(cls, options: dict[str, Any]) -> _ErrorProvider:
        return cls()

    async def generate(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        yield ChatEvent.message_start(message_id="m1", model=self.config.model)
        yield ChatEvent.error_event(error_type="upstream_server_error", error_message="500")


class _EchoTool(BaseTool):
    name: ClassVar[str] = "echo_tool"

    @classmethod
    def create(cls, entry: Any) -> _EchoTool:
        del entry
        return cls()

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "echo text back",
            "input_schema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        }

    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        text = input.get("text", "")
        return {"type": "tool_result", "content": [{"type": "text", "text": f"echoed: {text}"}]}


@pytest_asyncio.fixture
async def sessionmaker(tmp_path: Path):
    db_path = tmp_path / "chariot.db"
    sm = await init_db(db_path)
    try:
        yield sm
    finally:
        await dispose_db()


def _req(provider: str = "mock") -> ChatRequest:
    return ChatRequest(provider_ref=provider, messages=[Message(role="user", content="hi")])


class TestTraceIntegration:
    async def test_stateless_simple_turn_writes_trace(self, sessionmaker) -> None:
        agent = AIAgent(providers={"mock": _SimpleProvider()}, tools={}, sessionmaker=sessionmaker)
        events = [e async for e in agent.run_chat(_req())]
        assert any(e.kind == "stream_done" for e in events)

        async with sessionmaker() as session:
            turns = await TraceRepo(session).list_turns()
            assert len(turns) == 1
            turn = turns[0]
            assert turn.status == TurnStatus.COMPLETED
            assert turn.stop_reason == "end_turn"
            assert turn.provider_snapshot == "mock"
            assert turn.model == "mock-1"
            assert turn.input_tokens == 12
            assert turn.output_tokens == 5

            tree = await TraceRepo(session).get_tree(turn.id)
            assert tree is not None
            assert len(tree.provider_calls) == 1
            assert tree.provider_calls[0].response_summary["stop_reason"] == "end_turn"
            assert tree.provider_calls[0].latency_ms is not None
            assert len(tree.tool_calls) == 0

    async def test_tool_use_turn_records_tool_call(self, sessionmaker) -> None:
        agent = AIAgent(
            providers={"mock": _ToolUsingProvider()},
            tools={"echo_tool": _EchoTool()},
            sessionmaker=sessionmaker,
        )
        events = [e async for e in agent.run_chat(_req())]
        assert any(e.kind == "stream_done" for e in events)

        async with sessionmaker() as session:
            turns = await TraceRepo(session).list_turns()
            assert len(turns) == 1
            tree = await TraceRepo(session).get_tree(turns[0].id)
            assert tree is not None
            # 工具循环 2 轮 -> 2 个 provider call
            assert len(tree.provider_calls) == 2
            # 1 次工具调用
            assert len(tree.tool_calls) == 1
            tc = tree.tool_calls[0]
            assert tc.tool_name == "echo_tool"
            assert tc.status == ToolCallStatus.OK
            assert tc.arguments == {"text": "hello"}
            assert tc.result_summary is not None
            assert tc.result_summary["is_error"] is False
            assert "echoed: hello" in tc.result_summary["snippet"]

    async def test_provider_error_marks_turn_failed(self, sessionmaker) -> None:
        agent = AIAgent(providers={"mock": _ErrorProvider()}, tools={}, sessionmaker=sessionmaker)
        events = [e async for e in agent.run_chat(_req())]
        assert any(e.kind == "error" for e in events)

        async with sessionmaker() as session:
            turns = await TraceRepo(session).list_turns()
            assert len(turns) == 1
            turn = turns[0]
            assert turn.status == TurnStatus.FAILED
            assert turn.error_type == "upstream_server_error"

            tree = await TraceRepo(session).get_tree(turn.id)
            assert tree is not None
            # error 路径,provider call 也应记录错误
            assert len(tree.provider_calls) == 1
            assert tree.provider_calls[0].error_type == "upstream_server_error"

    async def test_unknown_provider_does_not_create_turn(self, sessionmaker) -> None:
        """provider 路由失败 -> 提早 yield error 退出,不应有 trace turn 写入。"""
        agent = AIAgent(providers={"mock": _SimpleProvider()}, tools={}, sessionmaker=sessionmaker)
        events = [e async for e in agent.run_chat(_req(provider="ghost"))]
        assert any(e.kind == "error" and e.error_type == "unknown_provider" for e in events)
        async with sessionmaker() as session:
            turns = await TraceRepo(session).list_turns()
            assert len(turns) == 0

    async def test_no_sessionmaker_no_trace(self) -> None:
        """sessionmaker=None -> TraceWriter no-op,主链路正常返事件,不抛。"""
        agent = AIAgent(providers={"mock": _SimpleProvider()}, tools={}, sessionmaker=None)
        events = [e async for e in agent.run_chat(_req())]
        assert any(e.kind == "stream_done" for e in events)

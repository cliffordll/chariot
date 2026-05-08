"""AgentLoop 单测。

覆盖(对照 FEATURE.md S.6 验收清单):
- 多轮:mock provider 给 [text+tool_use, text+tool_use, text-only] →
  断 events 含 3 个 message_start + 2 个 tool_result + 1 个 stream_done
- 工具异常:tool 抛 ValueError → tool_result(is_error=True, content 含 "x")
- max_iter 超:provider 永远给 stop_reason=tool_use → events 末尾
  error(error_type="agent_iter_exceeded")
- input_json 拼装:三片 partial_json → content_block_stop 拿到完整 input dict;
  parse 失败 → tool_result(is_error=True, content 含 "invalid_json")
- 空 assistant_blocks(无 tool_use)→ stream_done 直接收尾
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest, Message
from chariot.agent.loop import AgentLoop
from chariot.providers.base import BaseProvider, BaseProviderConfig
from chariot.tools.base import BaseTool

# ---------------------------------------------------------------------------
# Mock Provider:按预设 event 序列产 ChatEvent
# ---------------------------------------------------------------------------


class _ScriptedProvider(BaseProvider):
    """Mock provider:按预先设定的 turn 列表(每 turn 一个 ChatEvent 数组)yield。"""

    def __init__(self, turns: list[list[ChatEvent]]) -> None:
        self.config = BaseProviderConfig(name="scripted", model="scripted-1")
        self._turns = list(turns)

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> _ScriptedProvider:
        return cls(turns=[])

    async def generate(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        if not self._turns:
            return
        events = self._turns.pop(0)
        for ev in events:
            yield ev


def _text_turn() -> list[ChatEvent]:
    """一轮 text-only 响应(end_turn)。"""
    return [
        ChatEvent.message_start(message_id="msg_t", model="scripted-1"),
        ChatEvent.text_block_start(index=0),
        ChatEvent.text_delta("hello", index=0),
        ChatEvent.block_stop(index=0),
        ChatEvent.message_delta_done(stop_reason="end_turn"),
        ChatEvent.message_done(),
    ]


def _tool_use_turn(tool_use_id: str, tool_name: str, json_chunks: list[str]) -> list[ChatEvent]:
    """一轮 text + tool_use 响应(stop_reason=tool_use)。"""
    events: list[ChatEvent] = [
        ChatEvent.message_start(message_id=f"msg_{tool_use_id}", model="scripted-1"),
        ChatEvent.text_block_start(index=0),
        ChatEvent.text_delta("我查一下", index=0),
        ChatEvent.block_stop(index=0),
        ChatEvent.tool_use_block_start(index=1, tool_use_id=tool_use_id, tool_name=tool_name),
    ]
    for chunk in json_chunks:
        events.append(ChatEvent.input_json_delta(chunk, index=1))
    events.extend(
        [
            ChatEvent.block_stop(index=1),
            ChatEvent.message_delta_done(stop_reason="tool_use"),
            ChatEvent.message_done(),
        ]
    )
    return events


# ---------------------------------------------------------------------------
# Mock Tool:按预设结果返回
# ---------------------------------------------------------------------------


class _StubTool(BaseTool):
    """Mock tool:execute 返回预设 dict 或抛预设异常。"""

    def __init__(
        self,
        name: str,
        result: dict[str, Any] | None = None,
        exc: Exception | None = None,
    ) -> None:
        self.name = name
        self._result = result
        self._exc = exc
        self.last_input: dict[str, Any] | None = None

    @classmethod
    def from_config(cls, entry: Any) -> _StubTool:
        return cls(name="stub")

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "stub",
            "input_schema": {"type": "object", "properties": {}},
        }

    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        self.last_input = input
        if self._exc is not None:
            raise self._exc
        return self._result or {
            "type": "tool_result",
            "content": [{"type": "text", "text": "ok"}],
        }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def req() -> ChatRequest:
    return ChatRequest(
        provider_name="scripted",
        messages=[Message(role="user", content="hi")],
    )


def _make_loop(
    turns: list[list[ChatEvent]],
    tools: dict[str, BaseTool] | None = None,
    *,
    max_iter: int = 10,
) -> AgentLoop:
    return AgentLoop(
        provider=_ScriptedProvider(turns),
        tools=tools or {},
        repo=None,
        convo_id=None,
        max_iter=max_iter,
    )


# ---------------------------------------------------------------------------
# 多轮 + tool_result 注入
# ---------------------------------------------------------------------------


class TestMultiTurnWithToolUse:
    async def test_three_turn_sequence(self, req: ChatRequest) -> None:
        """3 轮:tool_use + tool_use + text-only,断 events 关键计数。"""
        turns = [
            _tool_use_turn("toolu_1", "stub", ['{"x":', "1", "}"]),
            _tool_use_turn("toolu_2", "stub", ['{"y":', "2", "}"]),
            _text_turn(),
        ]
        tools = {"stub": _StubTool("stub")}
        loop = _make_loop(turns, tools)

        events = [ev async for ev in loop.run(req)]
        kinds = [ev.kind for ev in events]
        assert kinds.count("message_start") == 3
        assert kinds.count("tool_result") == 2
        assert kinds.count("stream_done") == 1
        assert kinds[-1] == "stream_done"

    async def test_tool_result_payload(self, req: ChatRequest) -> None:
        """tool_result event 字段:tool_use_id / content / is_error=False。"""
        tools = {"stub": _StubTool("stub")}
        turns = [
            _tool_use_turn("toolu_abc", "stub", ['{"k":"v"}']),
            _text_turn(),
        ]
        loop = _make_loop(turns, tools)
        events = [ev async for ev in loop.run(req)]
        tr = next(e for e in events if e.kind == "tool_result")
        assert tr.tool_use_id == "toolu_abc"
        assert tr.is_error is False

    async def test_tool_input_parsed_correctly(self, req: ChatRequest) -> None:
        """input_json_delta 三片拼装后,Tool 拿到完整 dict。"""
        stub = _StubTool("stub")
        turns = [
            _tool_use_turn("toolu_1", "stub", ['{"path":', '"/tmp"', "}"]),
            _text_turn(),
        ]
        loop = _make_loop(turns, {"stub": stub})
        _ = [ev async for ev in loop.run(req)]
        assert stub.last_input == {"path": "/tmp"}


# ---------------------------------------------------------------------------
# 工具异常处理
# ---------------------------------------------------------------------------


class TestToolException:
    async def test_tool_raises_yields_error_result(self, req: ChatRequest) -> None:
        """tool 抛异常 → tool_result(is_error=True, content 含异常 message)。"""
        tools = {"stub": _StubTool("stub", exc=ValueError("simulated tool failure"))}
        turns = [
            _tool_use_turn("toolu_x", "stub", ["{}"]),
            _text_turn(),
        ]
        loop = _make_loop(turns, tools)
        events = [ev async for ev in loop.run(req)]
        tr = next(e for e in events if e.kind == "tool_result")
        assert tr.is_error is True
        assert "simulated tool failure" in str(tr.content)

    async def test_unknown_tool_yields_error_result(self, req: ChatRequest) -> None:
        """LLM 调了不存在的 tool → tool_result(is_error=True, content 含 'unknown tool')。"""
        turns = [
            _tool_use_turn("toolu_x", "ghost", ["{}"]),
            _text_turn(),
        ]
        loop = _make_loop(turns, tools={})  # 没注册 tool
        events = [ev async for ev in loop.run(req)]
        tr = next(e for e in events if e.kind == "tool_result")
        assert tr.is_error is True
        assert "unknown tool" in str(tr.content)


# ---------------------------------------------------------------------------
# input_json parse 失败
# ---------------------------------------------------------------------------


class TestInvalidInputJson:
    async def test_invalid_json_yields_error_result(self, req: ChatRequest) -> None:
        """LLM 流式给的 JSON 拼起来不合法 → tool_result(is_error=True,含 'invalid_json')。"""
        turns = [
            _tool_use_turn("toolu_x", "stub", ["not-a-json"]),
            _text_turn(),
        ]
        loop = _make_loop(turns, tools={"stub": _StubTool("stub")})
        events = [ev async for ev in loop.run(req)]
        tr = next(e for e in events if e.kind == "tool_result")
        assert tr.is_error is True
        assert "invalid_json" in str(tr.content)


# ---------------------------------------------------------------------------
# max_iter 超限
# ---------------------------------------------------------------------------


class TestMaxIterExceeded:
    async def test_yields_error_after_limit(self, req: ChatRequest) -> None:
        """provider 永远给 stop_reason=tool_use → events 末尾 error。"""
        # 准备超过 max_iter 的 turn,但 max_iter=2 让它快速失败
        turns = [_tool_use_turn(f"toolu_{i}", "stub", ["{}"]) for i in range(5)]
        tools = {"stub": _StubTool("stub")}
        loop = _make_loop(turns, tools, max_iter=2)
        events = [ev async for ev in loop.run(req)]
        last = events[-1]
        assert last.kind == "error"
        assert last.error_type == "agent_iter_exceeded"


# ---------------------------------------------------------------------------
# 单轮 text-only 收尾
# ---------------------------------------------------------------------------


class TestSingleTurnTextOnly:
    async def test_no_tool_use_stream_done(self, req: ChatRequest) -> None:
        """无 tool_use 直接 end_turn → stream_done 收尾,只 1 个 message_start。"""
        loop = _make_loop([_text_turn()])
        events = [ev async for ev in loop.run(req)]
        kinds = [ev.kind for ev in events]
        assert kinds.count("message_start") == 1
        assert "tool_result" not in kinds
        assert kinds[-1] == "stream_done"


# ---------------------------------------------------------------------------
# Provider 内部 yield error event → AgentLoop 直接 return
# ---------------------------------------------------------------------------


class TestProviderError:
    async def test_provider_error_event_terminates_loop(self, req: ChatRequest) -> None:
        """provider 流中 yield kind=error → AgentLoop 不产 stream_done,直接终结。"""
        events_seq = [
            ChatEvent.message_start(message_id="msg_x", model="scripted-1"),
            ChatEvent.error_event(
                error_type="upstream_stream_error",
                error_message="simulated mid-stream",
            ),
        ]
        loop = _make_loop([events_seq])
        events = [ev async for ev in loop.run(req)]
        kinds = [ev.kind for ev in events]
        # 末尾是 error,不是 stream_done(AgentLoop 不补)
        assert kinds == ["message_start", "error"]

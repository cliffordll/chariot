"""ChatEvent 单测。

覆盖:
- frozen 不可变
- kind dispatch 完整性(覆盖全部 10 种)—— 漏一个 kind 静态分析能查到
- 工厂方法返回的字段正确(跟 Claude SSE payload 1:1)
- 工厂方法名 / 参数符合 DESIGN §3.2
"""

from __future__ import annotations

import dataclasses

import pytest

from chariot.agent.chat_event import ChatEvent, ChatEventKind

# 全部 10 种 kind(测试断言用,**漏一种新加 kind 时这里编译失败**)
ALL_KINDS: tuple[ChatEventKind, ...] = (
    "message_start",
    "content_block_start",
    "content_block_delta",
    "content_block_stop",
    "message_delta",
    "message_stop",
    "ping",
    "error",
    "tool_result",
    "stream_done",
)


class TestChatEventFrozen:
    def test_frozen(self) -> None:
        ev = ChatEvent.text_delta("hi")
        with pytest.raises(dataclasses.FrozenInstanceError):
            ev.kind = "message_start"  # type: ignore[misc]


class TestKindDispatchCompleteness:
    """match event.kind 必须覆盖全部 10 种 kind。"""

    def test_all_kinds_match(self) -> None:
        """构造每种 kind 后 dispatch,确保没漏。"""
        events: list[ChatEvent] = [
            ChatEvent.message_start(message_id="msg_1", model="claude"),
            ChatEvent.text_block_start(index=0),
            ChatEvent.text_delta("hi"),
            ChatEvent.block_stop(index=0),
            ChatEvent.message_delta_done(stop_reason="end_turn"),
            ChatEvent.message_done(),
            ChatEvent.ping_event(),
            ChatEvent.error_event(error_type="upstream_auth_failed", error_message="x"),
            ChatEvent.tool_result_event(tool_use_id="toolu_1", content="ok"),
            ChatEvent.stream_done_event(),
        ]
        seen: set[str] = set()
        for ev in events:
            match ev.kind:
                case "message_start":
                    seen.add("message_start")
                case "content_block_start":
                    seen.add("content_block_start")
                case "content_block_delta":
                    seen.add("content_block_delta")
                case "content_block_stop":
                    seen.add("content_block_stop")
                case "message_delta":
                    seen.add("message_delta")
                case "message_stop":
                    seen.add("message_stop")
                case "ping":
                    seen.add("ping")
                case "error":
                    seen.add("error")
                case "tool_result":
                    seen.add("tool_result")
                case "stream_done":
                    seen.add("stream_done")
        assert seen == set(ALL_KINDS)


class TestFactoryMessageStart:
    def test_fields(self) -> None:
        ev = ChatEvent.message_start(
            message_id="msg_abc",
            model="claude-sonnet-4-6",
            usage={"input_tokens": 25, "output_tokens": 1},
        )
        assert ev.kind == "message_start"
        assert ev.message is not None
        assert ev.message["id"] == "msg_abc"
        assert ev.message["model"] == "claude-sonnet-4-6"
        assert ev.message["role"] == "assistant"
        assert ev.message["type"] == "message"
        assert ev.usage == {"input_tokens": 25, "output_tokens": 1}


class TestFactoryContentBlockStart:
    def test_text_block(self) -> None:
        ev = ChatEvent.text_block_start(index=0)
        assert ev.kind == "content_block_start"
        assert ev.index == 0
        assert ev.content_block == {"type": "text", "text": ""}

    def test_tool_use_block(self) -> None:
        ev = ChatEvent.tool_use_block_start(
            index=1,
            tool_use_id="toolu_xyz",
            tool_name="read_file",
        )
        assert ev.kind == "content_block_start"
        assert ev.index == 1
        assert ev.content_block is not None
        assert ev.content_block["type"] == "tool_use"
        assert ev.content_block["id"] == "toolu_xyz"
        assert ev.content_block["name"] == "read_file"


class TestFactoryContentBlockDelta:
    def test_text_delta(self) -> None:
        ev = ChatEvent.text_delta("hello")
        assert ev.kind == "content_block_delta"
        assert ev.index == 0
        assert ev.delta == {"type": "text_delta", "text": "hello"}

    def test_text_delta_at_index(self) -> None:
        ev = ChatEvent.text_delta("x", index=2)
        assert ev.index == 2

    def test_input_json_delta(self) -> None:
        ev = ChatEvent.input_json_delta('{"path":', index=1)
        assert ev.kind == "content_block_delta"
        assert ev.index == 1
        assert ev.delta == {"type": "input_json_delta", "partial_json": '{"path":'}


class TestFactoryBlockStop:
    def test_index(self) -> None:
        ev = ChatEvent.block_stop(index=2)
        assert ev.kind == "content_block_stop"
        assert ev.index == 2


class TestFactoryMessageDelta:
    def test_with_usage(self) -> None:
        ev = ChatEvent.message_delta_done(
            stop_reason="tool_use",
            usage={"output_tokens": 42},
        )
        assert ev.kind == "message_delta"
        assert ev.delta == {"stop_reason": "tool_use", "stop_sequence": None}
        assert ev.usage == {"output_tokens": 42}

    def test_with_stop_sequence(self) -> None:
        ev = ChatEvent.message_delta_done(stop_reason="stop_sequence", stop_sequence="END")
        assert ev.delta == {"stop_reason": "stop_sequence", "stop_sequence": "END"}


class TestFactoryMessageStop:
    def test_no_fields(self) -> None:
        ev = ChatEvent.message_done()
        assert ev.kind == "message_stop"


class TestFactoryPing:
    def test_no_fields(self) -> None:
        ev = ChatEvent.ping_event()
        assert ev.kind == "ping"


class TestFactoryError:
    def test_fields(self) -> None:
        ev = ChatEvent.error_event(error_type="upstream_auth_failed", error_message="401")
        assert ev.kind == "error"
        assert ev.error_type == "upstream_auth_failed"
        assert ev.error_message == "401"


class TestFactoryToolResult:
    def test_success(self) -> None:
        ev = ChatEvent.tool_result_event(tool_use_id="toolu_1", content="file contents")
        assert ev.kind == "tool_result"
        assert ev.tool_use_id == "toolu_1"
        assert ev.content == "file contents"
        assert ev.is_error is False

    def test_error(self) -> None:
        ev = ChatEvent.tool_result_event(
            tool_use_id="toolu_2",
            content="permission denied",
            is_error=True,
        )
        assert ev.is_error is True


class TestFactoryStreamDone:
    def test_no_fields(self) -> None:
        ev = ChatEvent.stream_done_event()
        assert ev.kind == "stream_done"

"""Renderer.render_event dispatcher 测试(0.6.0 库化版)。

`Renderer.render_event` 是 REPL / once / batch 共用的 ChatEvent → 屏幕分派。
回放 ChatEvent 序列,断 Renderer 各 sink 方法被以正确参数调到。

不测真实 stdout 输出形态(由 rich console 控制,跨平台输出字节不稳)。
"""

from __future__ import annotations

from collections.abc import Callable, Generator

import pytest

from chariot.agent.chat_event import ChatEvent
from chariot.cli.render import Renderer

_Calls = list[tuple[str, tuple[object, ...], dict[str, object]]]


@pytest.fixture(autouse=True)
def _capture_renderer_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[_Calls, None, None]:
    """把 stream_token / tool_use_line / tool_result_line 替成记录器,避免动 stdout。

    fixture 进入前 + 退出后都重置 Renderer 内部 buffer,避免跨 case 串味。
    """
    calls: _Calls = []

    def _spy(name: str) -> Callable[..., None]:
        def f(*args: object, **kwargs: object) -> None:
            calls.append((name, args, kwargs))

        return f

    monkeypatch.setattr(Renderer, "stream_token", _spy("stream_token"))
    monkeypatch.setattr(Renderer, "tool_use_line", _spy("tool_use_line"))
    monkeypatch.setattr(Renderer, "tool_result_line", _spy("tool_result_line"))
    Renderer._reset_for_tests()
    yield calls
    Renderer._reset_for_tests()


def test_text_delta_event_calls_stream_token(
    _capture_renderer_calls: _Calls,
) -> None:
    """content_block_delta(text_delta) → stream_token(text)。"""
    Renderer.render_event(ChatEvent.text_delta("hello", index=0))
    assert _capture_renderer_calls == [("stream_token", ("hello",), {})]


def test_tool_use_block_emits_tool_use_line_at_stop(
    _capture_renderer_calls: _Calls,
) -> None:
    """tool_use 块流式三段:start → input_json_delta * N → stop。
    Renderer 在 stop 时一次性输出 tool_use_line(name + 拼装好的 input_json)。
    """
    Renderer.render_event(
        ChatEvent.tool_use_block_start(index=1, tool_use_id="tu_1", tool_name="read_file"),
    )
    # 拼 input_json
    Renderer.render_event(ChatEvent.input_json_delta('{"path":', index=1))
    Renderer.render_event(ChatEvent.input_json_delta('"x.txt"}', index=1))
    # stop 触发输出
    Renderer.render_event(ChatEvent.block_stop(index=1))

    assert len(_capture_renderer_calls) == 1
    name, args, kwargs = _capture_renderer_calls[0]
    assert name == "tool_use_line"
    assert kwargs == {}
    assert args[0] == "read_file"
    assert args[1] == '{"path":"x.txt"}'


def test_tool_use_block_with_no_input_json_uses_empty_dict(
    _capture_renderer_calls: _Calls,
) -> None:
    """tool_use 没有 input_json_delta(零参工具)→ 退化为 `{}`。"""
    Renderer.render_event(
        ChatEvent.tool_use_block_start(index=2, tool_use_id="tu_2", tool_name="ping"),
    )
    Renderer.render_event(ChatEvent.block_stop(index=2))

    name, args, _ = _capture_renderer_calls[0]
    assert name == "tool_use_line"
    assert args == ("ping", "{}")


def test_text_block_stop_does_not_emit_tool_use_line(
    _capture_renderer_calls: _Calls,
) -> None:
    """text 块 stop 不触发 tool_use_line(只在 tool_use 块的 stop 触发)。"""
    Renderer.render_event(ChatEvent.text_block_start(index=0))
    Renderer.render_event(ChatEvent.text_delta("hi", index=0))
    Renderer.render_event(ChatEvent.block_stop(index=0))
    # 仅一次 stream_token,无 tool_use_line
    sinks = [c[0] for c in _capture_renderer_calls]
    assert sinks == ["stream_token"]


def test_tool_result_event_calls_tool_result_line_with_string_content(
    _capture_renderer_calls: _Calls,
) -> None:
    """tool_result.content 是字符串 → 直接传 tool_result_line。"""
    Renderer.render_event(
        ChatEvent.tool_result_event(
            tool_use_id="tu_1",
            content="文件内容...",
            is_error=False,
        ),
    )
    assert _capture_renderer_calls == [
        ("tool_result_line", ("文件内容...",), {"is_error": False}),
    ]


def test_tool_result_event_with_block_content_extracts_text(
    _capture_renderer_calls: _Calls,
) -> None:
    """tool_result.content 是 list[block] → 抽 type=text 的 text 字段拼接。"""
    Renderer.render_event(
        ChatEvent.tool_result_event(
            tool_use_id="tu_1",
            content=[
                {"type": "text", "text": "line 1"},
                {"type": "text", "text": "line 2"},
            ],
            is_error=False,
        ),
    )
    name, args, kwargs = _capture_renderer_calls[0]
    assert name == "tool_result_line"
    assert args == ("line 1\nline 2",)
    assert kwargs == {"is_error": False}


def test_tool_result_error_passes_is_error_true(
    _capture_renderer_calls: _Calls,
) -> None:
    """is_error=True 透传到 tool_result_line。"""
    Renderer.render_event(
        ChatEvent.tool_result_event(
            tool_use_id="tu_2",
            content="ENOENT",
            is_error=True,
        ),
    )
    assert _capture_renderer_calls == [
        ("tool_result_line", ("ENOENT",), {"is_error": True}),
    ]


def test_message_lifecycle_events_dont_emit_sinks(
    _capture_renderer_calls: _Calls,
) -> None:
    """message_start / message_delta / message_stop / ping / stream_done / error
    都不触发 sink 方法(meta 行另外打,_close_live 由 dispatch 内部调)。"""
    Renderer.render_event(ChatEvent.message_start(message_id="m_1", model="mock"))
    Renderer.render_event(ChatEvent.message_delta_done(stop_reason="end_turn"))
    Renderer.render_event(ChatEvent.message_done())
    Renderer.render_event(ChatEvent.ping_event())
    Renderer.render_event(ChatEvent.stream_done_event())
    Renderer.render_event(
        ChatEvent.error_event(error_type="upstream_auth_failed", error_message="bad"),
    )
    assert _capture_renderer_calls == []


def test_full_dispatch_sequence(
    _capture_renderer_calls: _Calls,
) -> None:
    """完整一轮工具循环的 ChatEvent 序列。"""
    events = [
        ChatEvent.message_start(message_id="m_1", model="mock"),
        ChatEvent.text_block_start(index=0),
        ChatEvent.text_delta("让我查一下", index=0),
        ChatEvent.block_stop(index=0),
        ChatEvent.tool_use_block_start(index=1, tool_use_id="tu_1", tool_name="read_file"),
        ChatEvent.input_json_delta('{"path":"x.txt"}', index=1),
        ChatEvent.block_stop(index=1),
        ChatEvent.message_delta_done(stop_reason="tool_use"),
        ChatEvent.message_done(),
        ChatEvent.tool_result_event(tool_use_id="tu_1", content="hello world"),
        # 下一轮
        ChatEvent.message_start(message_id="m_2", model="mock"),
        ChatEvent.text_block_start(index=0),
        ChatEvent.text_delta("文件内容是 hello world", index=0),
        ChatEvent.block_stop(index=0),
        ChatEvent.message_delta_done(stop_reason="end_turn"),
        ChatEvent.message_done(),
        ChatEvent.stream_done_event(),
    ]
    for ev in events:
        Renderer.render_event(ev)

    sinks = [c[0] for c in _capture_renderer_calls]
    # 期望:text → tool_use → tool_result → text(message_* / stream_done 静默)
    assert sinks == [
        "stream_token",
        "tool_use_line",
        "tool_result_line",
        "stream_token",
    ]

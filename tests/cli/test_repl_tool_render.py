"""Renderer.render_event dispatcher 测试(0.5.0 S.4)。

`Renderer.render_event` 是 REPL / once / batch 共用的 typed event → 屏幕分派。
回放 StreamEvent 序列,断 Renderer 各 sink 方法被以正确参数调到。

不测真实 stdout 输出形态(由 rich console 控制,跨平台输出字节不稳)。
"""

from __future__ import annotations

import pytest

from chariot.cli.core.render import Renderer
from chariot.sdk.streams import StreamEvent


@pytest.fixture(autouse=True)
def _capture_renderer_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, tuple[object, ...], dict[str, object]]]:
    """把 stream_token / tool_use_line / tool_result_line 替成记录器,避免动 stdout。"""
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def _spy(name: str):
        def f(*args: object, **kwargs: object) -> None:
            calls.append((name, args, kwargs))

        return f

    monkeypatch.setattr(Renderer, "stream_token", _spy("stream_token"))
    monkeypatch.setattr(Renderer, "tool_use_line", _spy("tool_use_line"))
    monkeypatch.setattr(Renderer, "tool_result_line", _spy("tool_result_line"))
    return calls


def test_text_event_calls_stream_token(
    _capture_renderer_calls: list[tuple[str, tuple[object, ...], dict[str, object]]],
) -> None:
    """kind=text → stream_token(text)。"""
    Renderer.render_event(StreamEvent(kind="text", text="hello"))
    assert _capture_renderer_calls == [("stream_token", ("hello",), {})]


def test_tool_use_event_calls_tool_use_line_with_json_input(
    _capture_renderer_calls: list[tuple[str, tuple[object, ...], dict[str, object]]],
) -> None:
    """kind=tool_use → tool_use_line(name, json.dumps(input))。"""
    Renderer.render_event(
        StreamEvent(
            kind="tool_use",
            tool_use_id="tu_1",
            tool_name="read_file",
            tool_input={"path": "x.txt"},
        ),
    )
    assert len(_capture_renderer_calls) == 1
    name, args, kwargs = _capture_renderer_calls[0]
    assert name == "tool_use_line"
    assert kwargs == {}
    assert args[0] == "read_file"
    # input 序列化:JSON 紧凑,中文不转义(ensure_ascii=False)
    assert args[1] == '{"path": "x.txt"}'


def test_tool_use_event_with_none_input_serializes_empty_dict(
    _capture_renderer_calls: list[tuple[str, tuple[object, ...], dict[str, object]]],
) -> None:
    """tool_input=None(协议边界,理论不出现)→ 序列化成 `{}`。"""
    Renderer.render_event(
        StreamEvent(kind="tool_use", tool_name="t", tool_input=None),
    )
    name, args, _ = _capture_renderer_calls[0]
    assert name == "tool_use_line"
    assert args == ("t", "{}")


def test_tool_result_event_calls_tool_result_line(
    _capture_renderer_calls: list[tuple[str, tuple[object, ...], dict[str, object]]],
) -> None:
    """kind=tool_result(成功)→ tool_result_line(text, is_error=False)。"""
    Renderer.render_event(
        StreamEvent(
            kind="tool_result",
            tool_use_id="tu_1",
            tool_result_content="文件内容...",
            is_error=False,
        ),
    )
    assert _capture_renderer_calls == [
        ("tool_result_line", ("文件内容...",), {"is_error": False}),
    ]


def test_tool_result_error_passes_is_error_true(
    _capture_renderer_calls: list[tuple[str, tuple[object, ...], dict[str, object]]],
) -> None:
    """is_error=True 透传到 tool_result_line。"""
    Renderer.render_event(
        StreamEvent(kind="tool_result", tool_result_content="ENOENT", is_error=True),
    )
    assert _capture_renderer_calls == [
        ("tool_result_line", ("ENOENT",), {"is_error": True}),
    ]


def test_turn_complete_and_stream_done_silent(
    _capture_renderer_calls: list[tuple[str, tuple[object, ...], dict[str, object]]],
) -> None:
    """turn_complete / stream_done 不触发任何 Renderer sink(meta 行另外打)。"""
    Renderer.render_event(StreamEvent(kind="turn_complete", role="assistant"))
    Renderer.render_event(StreamEvent(kind="stream_done"))
    assert _capture_renderer_calls == []


def test_full_dispatch_sequence(
    _capture_renderer_calls: list[tuple[str, tuple[object, ...], dict[str, object]]],
) -> None:
    """完整一轮工具循环的事件序列:多 text + tool_use + tool_result + final text。"""
    events = [
        StreamEvent(kind="text", text="让我查一下"),
        StreamEvent(
            kind="tool_use",
            tool_name="read_file",
            tool_input={"path": "x.txt"},
        ),
        StreamEvent(
            kind="turn_complete",
            role="assistant",
        ),
        StreamEvent(
            kind="tool_result",
            tool_use_id="tu_1",
            tool_result_content="hello world",
            is_error=False,
        ),
        StreamEvent(kind="turn_complete", role="user"),
        StreamEvent(kind="text", text="文件内容是 hello world"),
        StreamEvent(kind="turn_complete", role="assistant"),
        StreamEvent(kind="stream_done"),
    ]
    for ev in events:
        Renderer.render_event(ev)

    sinks = [c[0] for c in _capture_renderer_calls]
    # 期望:text → tool_use → tool_result → text(turn_complete / stream_done 静默)
    assert sinks == [
        "stream_token",
        "tool_use_line",
        "tool_result_line",
        "stream_token",
    ]

"""ChatStream typed events 测试(0.5.0 S.2)。

回放手写的 Anthropic SSE 字节序列 → 断 events 序列正确。覆盖:
- text-only 单 turn
- tool_use(累 input_json_delta + JSON 解析)
- tool_result(server 合成的 user role message,content 整块塞)
- 多 turn(slow path):跨多个 message_start 累加 usage,只对 role=assistant 累
- text_deltas 向后兼容 wrapper
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from chariot.sdk.streams import ChatStream, StreamEvent

# ============================================================
# 测试工具:fake httpx.Response + SSE 帧构造
# ============================================================


class _FakeResponse:
    """ChatStream 只调用 `resp.aiter_bytes()`,任何带这个 async 方法的对象都能用。"""

    def __init__(self, byte_chunks: list[bytes]) -> None:
        self._chunks = byte_chunks

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        for c in self._chunks:
            yield c


def _frame(name: str, data: dict[str, Any]) -> bytes:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n".encode()


def _build_assistant_text_turn(text: str, *, msg_id: str = "msg_a") -> list[bytes]:
    """单 text block 的 assistant turn(message_start → text_delta → message_stop)。"""
    return [
        _frame(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": msg_id,
                    "type": "message",
                    "role": "assistant",
                    "model": "spy",
                    "content": [],
                    "stop_reason": None,
                    "usage": {"input_tokens": 100, "output_tokens": 0},
                },
            },
        ),
        _frame(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        _frame(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            },
        ),
        _frame("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _frame(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 50},
            },
        ),
        _frame("message_stop", {"type": "message_stop"}),
    ]


def _build_tool_use_turn(
    tu_id: str,
    tu_name: str,
    tu_input: dict[str, Any],
    *,
    msg_id: str = "msg_tu",
) -> list[bytes]:
    """assistant turn 含 tool_use block(input 通过 input_json_delta 累积)。"""
    input_json = json.dumps(tu_input)
    # 切两段验证累积逻辑
    half = len(input_json) // 2
    part1, part2 = input_json[:half], input_json[half:]
    return [
        _frame(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": msg_id,
                    "type": "message",
                    "role": "assistant",
                    "model": "spy",
                    "content": [],
                    "stop_reason": None,
                    "usage": {"input_tokens": 80, "output_tokens": 0},
                },
            },
        ),
        _frame(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": tu_id,
                    "name": tu_name,
                    "input": {},
                },
            },
        ),
        _frame(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": part1},
            },
        ),
        _frame(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": part2},
            },
        ),
        _frame("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _frame(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"output_tokens": 30},
            },
        ),
        _frame("message_stop", {"type": "message_stop"}),
    ]


def _build_synthetic_tool_result_message(
    tu_id: str,
    text: str,
    *,
    is_error: bool = False,
) -> list[bytes]:
    """模拟 server 合成的 tool_result message(role=user,content 整块塞 content_block_start)。"""
    return [
        _frame(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": f"msg_chariot_tu_{tu_id}",
                    "type": "message",
                    "role": "user",
                    "model": "chariot",
                    "content": [],
                    "stop_reason": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            },
        ),
        _frame(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_result",
                    "tool_use_id": tu_id,
                    "content": [{"type": "text", "text": text}],
                    "is_error": is_error,
                },
            },
        ),
        _frame("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _frame(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 0},
            },
        ),
        _frame("message_stop", {"type": "message_stop"}),
    ]


async def _collect_events(stream: ChatStream, frames: list[bytes]) -> list[StreamEvent]:
    resp = _FakeResponse([b"".join(frames)])
    return [ev async for ev in stream.events(resp)]  # type: ignore[arg-type]


# ============================================================
# text-only 单 turn
# ============================================================


async def test_events_text_only_single_turn() -> None:
    """单 turn text-only:events 应是 [text, turn_complete, stream_done]。"""
    stream = ChatStream()
    frames = _build_assistant_text_turn("hello world")
    events = await _collect_events(stream, frames)

    kinds = [ev.kind for ev in events]
    assert kinds == ["text", "turn_complete", "stream_done"]
    assert events[0].text == "hello world"
    assert events[1].role == "assistant"
    assert events[1].input_tokens == 100
    assert events[1].output_tokens == 50
    # stream_done 报累计总和(单 turn → 同 turn 值)
    assert events[2].input_tokens == 100
    assert events[2].output_tokens == 50
    # 实例字段也累加好
    assert stream.input_tokens == 100
    assert stream.output_tokens == 50


# ============================================================
# tool_use(累 input_json_delta + JSON 解析)
# ============================================================


async def test_events_tool_use_emits_complete_block() -> None:
    """tool_use block:input_json_delta 切两段 → 累积后 parse → 完整 dict 出现在 tool_input。"""
    stream = ChatStream()
    frames = _build_tool_use_turn(
        tu_id="tu_001",
        tu_name="read_file",
        tu_input={"path": "/etc/hosts", "max_bytes": 1024},
    )
    events = await _collect_events(stream, frames)

    # tool_use_complete 在 content_block_stop 一次性吐(累完才有完整 input)
    tool_evts = [ev for ev in events if ev.kind == "tool_use"]
    assert len(tool_evts) == 1
    tu = tool_evts[0]
    assert tu.tool_use_id == "tu_001"
    assert tu.tool_name == "read_file"
    assert tu.tool_input == {"path": "/etc/hosts", "max_bytes": 1024}
    # 没 text 增量
    assert not any(ev.kind == "text" for ev in events)


async def test_events_tool_use_invalid_json_falls_back_to_empty() -> None:
    """input_json_delta 拼不出合法 JSON → tool_input = {} (不抛)。"""
    stream = ChatStream()
    frames = [
        _frame(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "m",
                    "type": "message",
                    "role": "assistant",
                    "model": "spy",
                    "content": [],
                    "stop_reason": None,
                    "usage": {"input_tokens": 1, "output_tokens": 0},
                },
            },
        ),
        _frame(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "tu_x",
                    "name": "broken",
                    "input": {},
                },
            },
        ),
        _frame(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": "{not valid"},
            },
        ),
        _frame("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _frame("message_stop", {"type": "message_stop"}),
    ]
    events = await _collect_events(stream, frames)
    tu = next(ev for ev in events if ev.kind == "tool_use")
    assert tu.tool_input == {}


# ============================================================
# tool_result(server 合成的 user message)
# ============================================================


async def test_events_tool_result_extracts_content_text() -> None:
    """tool_result block(content 是 list[text block])→ 拼成 tool_result_content 字符串。"""
    stream = ChatStream()
    frames = _build_synthetic_tool_result_message("tu_001", "文件内容前 1024 字节...")
    events = await _collect_events(stream, frames)

    tr_evts = [ev for ev in events if ev.kind == "tool_result"]
    assert len(tr_evts) == 1
    tr = tr_evts[0]
    assert tr.tool_use_id == "tu_001"
    assert tr.tool_result_content == "文件内容前 1024 字节..."
    assert tr.is_error is False
    # role=user 的 turn_complete 也会发一条
    tc = next(ev for ev in events if ev.kind == "turn_complete")
    assert tc.role == "user"
    # 但 user role 的 usage 不计入 stream 累计
    assert stream.input_tokens == 0
    assert stream.output_tokens == 0


async def test_events_tool_result_is_error_flag() -> None:
    """is_error=True 透传。"""
    stream = ChatStream()
    frames = _build_synthetic_tool_result_message("tu_x", "文件不存在", is_error=True)
    events = await _collect_events(stream, frames)
    tr = next(ev for ev in events if ev.kind == "tool_result")
    assert tr.is_error is True


async def test_events_tool_result_content_as_string() -> None:
    """tool_result.content 也允许是 str(协议允许)。"""
    stream = ChatStream()
    frames = [
        _frame(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "m",
                    "type": "message",
                    "role": "user",
                    "model": "chariot",
                    "content": [],
                    "stop_reason": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            },
        ),
        _frame(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_result",
                    "tool_use_id": "tu_y",
                    "content": "纯字符串结果",
                },
            },
        ),
        _frame("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _frame("message_stop", {"type": "message_stop"}),
    ]
    events = await _collect_events(stream, frames)
    tr = next(ev for ev in events if ev.kind == "tool_result")
    assert tr.tool_result_content == "纯字符串结果"


# ============================================================
# 多 turn(slow path:turn1 tool_use → tool_result → turn2 text)
# ============================================================


async def test_events_multi_turn_slow_path() -> None:
    """完整 slow path 流:assistant tool_use turn → 合成 tool_result message →
    assistant final text turn。

    断:
    - 3 个 turn_complete(assistant / user / assistant)
    - tool_use + tool_result 各一
    - usage 只累加 assistant 两轮(80+30 + 60+40 = 140 / 70)
    """
    stream = ChatStream()
    turn1 = _build_tool_use_turn(
        tu_id="tu_a",
        tu_name="read_file",
        tu_input={"path": "x"},
    )
    tr_msg = _build_synthetic_tool_result_message("tu_a", "file contents")
    # 第二轮 final text,自定义 usage 60/40
    turn2 = [
        _frame(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_final",
                    "type": "message",
                    "role": "assistant",
                    "model": "spy",
                    "content": [],
                    "stop_reason": None,
                    "usage": {"input_tokens": 60, "output_tokens": 0},
                },
            },
        ),
        _frame(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        _frame(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "based on the file: 42"},
            },
        ),
        _frame("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _frame(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 40},
            },
        ),
        _frame("message_stop", {"type": "message_stop"}),
    ]

    events = await _collect_events(stream, turn1 + tr_msg + turn2)

    # turn_complete 出现 3 次,role 序列 [assistant, user, assistant]
    tcs = [ev for ev in events if ev.kind == "turn_complete"]
    assert [tc.role for tc in tcs] == ["assistant", "user", "assistant"]

    # tool_use / tool_result 各一
    tus = [ev for ev in events if ev.kind == "tool_use"]
    trs = [ev for ev in events if ev.kind == "tool_result"]
    assert len(tus) == 1 and len(trs) == 1
    assert tus[0].tool_input == {"path": "x"}
    assert trs[0].tool_result_content == "file contents"

    # text 只出现在 turn2(final)
    texts = [ev for ev in events if ev.kind == "text"]
    assert "".join(t.text for t in texts) == "based on the file: 42"

    # 跨 turn usage 累加(只 assistant)
    assert stream.input_tokens == 80 + 60
    assert stream.output_tokens == 30 + 40

    # stream_done 报累计总和
    sd = events[-1]
    assert sd.kind == "stream_done"
    assert sd.input_tokens == 140
    assert sd.output_tokens == 70


# ============================================================
# text_deltas 向后兼容
# ============================================================


async def test_text_deltas_yields_only_text_events() -> None:
    """text_deltas 是 events() 的薄 wrapper:只过滤 text 事件 yield text 字段。"""
    stream = ChatStream()
    frames = _build_assistant_text_turn("hello")
    resp = _FakeResponse([b"".join(frames)])

    tokens: list[str] = []
    async for tok in stream.text_deltas(resp):  # type: ignore[arg-type]
        tokens.append(tok)
    assert tokens == ["hello"]
    # 流后实例字段累加好
    assert stream.input_tokens == 100
    assert stream.output_tokens == 50


async def test_text_deltas_skips_tool_blocks() -> None:
    """text_deltas 不该把 tool_use / tool_result 误当 text 吐。"""
    stream = ChatStream()
    frames = (
        _build_tool_use_turn(tu_id="tu", tu_name="t", tu_input={"a": 1})
        + _build_synthetic_tool_result_message("tu", "result")
        + _build_assistant_text_turn("ok", msg_id="msg_final")
    )
    resp = _FakeResponse([b"".join(frames)])
    tokens = [tok async for tok in stream.text_deltas(resp)]  # type: ignore[arg-type]
    assert tokens == ["ok"]

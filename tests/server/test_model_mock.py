"""MockModel 测试 —— 三协议 echo 响应契约(结构 + 文本)。"""

from __future__ import annotations

import json

import pytest
from fastapi.responses import Response, StreamingResponse

from chariot.server.model.mock import MockModel
from chariot.server.service.exceptions import ServiceError
from chariot.shared.protocols import Protocol


async def _drain_stream(resp: Response) -> str:
    assert isinstance(resp, StreamingResponse)
    chunks: list[bytes] = []
    async for c in resp.body_iterator:
        if isinstance(c, bytes):
            chunks.append(c)
        elif isinstance(c, str):
            chunks.append(c.encode("utf-8"))
        else:
            chunks.append(bytes(c))
    return b"".join(chunks).decode("utf-8")


# ---------- 非流式响应 schema ----------


async def test_messages_non_stream_schema() -> None:
    m = MockModel()
    body = json.dumps(
        {"model": "x", "max_tokens": 64, "messages": [{"role": "user", "content": "hi"}]}
    ).encode("utf-8")
    resp = await m.respond(Protocol.MESSAGES, body, stream=False)
    data = json.loads(bytes(resp.body))

    assert data["type"] == "message"
    assert data["role"] == "assistant"
    assert data["stop_reason"] == "end_turn"
    assert data["content"][0]["type"] == "text"
    assert data["content"][0]["text"].startswith("[mock echo]")
    assert data["content"][0]["text"].endswith("hi")
    assert data["model"] == "mock-echo-v1"


async def test_completions_non_stream_schema() -> None:
    m = MockModel()
    body = json.dumps({"model": "x", "messages": [{"role": "user", "content": "hey"}]}).encode(
        "utf-8"
    )
    resp = await m.respond(Protocol.CHAT_COMPLETIONS, body, stream=False)
    data = json.loads(bytes(resp.body))

    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert data["choices"][0]["message"]["content"].startswith("[mock echo]")
    assert data["choices"][0]["finish_reason"] == "stop"


async def test_responses_non_stream_schema() -> None:
    m = MockModel()
    body = json.dumps({"model": "x", "input": "ping"}).encode("utf-8")
    resp = await m.respond(Protocol.RESPONSES, body, stream=False)
    data = json.loads(bytes(resp.body))

    assert data["object"] == "response"
    assert data["status"] == "completed"
    assert data["output"][0]["type"] == "message"
    assert data["output"][0]["content"][0]["type"] == "output_text"
    assert data["output_text"].startswith("[mock echo]")


# ---------- 流式响应关键事件 ----------


async def test_messages_stream_has_required_events() -> None:
    m = MockModel()
    body = json.dumps(
        {
            "model": "x",
            "max_tokens": 64,
            "stream": True,
            "messages": [{"role": "user", "content": "hello"}],
        }
    ).encode("utf-8")
    resp = await m.respond(Protocol.MESSAGES, body, stream=True)
    assert isinstance(resp, StreamingResponse)
    raw = await _drain_stream(resp)

    for tag in (
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ):
        assert tag in raw, f"stream missing event {tag!r}"
    # 文本被 chunk 成 4 字符一段,不在任何单个 chunk 里完整出现
    # 但 "mock" 短到会出现在某个 delta 里
    assert '"text": "[mo' in raw or '"text": "mo' in raw  # 第一个 chunk 带 [mo 或 mo
    assert "hello"[:3] in raw


async def test_completions_stream_ends_with_done() -> None:
    m = MockModel()
    body = json.dumps(
        {"model": "x", "stream": True, "messages": [{"role": "user", "content": "yo"}]}
    ).encode("utf-8")
    resp = await m.respond(Protocol.CHAT_COMPLETIONS, body, stream=True)
    raw = await _drain_stream(resp)

    assert '"finish_reason": "stop"' in raw
    assert raw.rstrip().endswith("data: [DONE]")


async def test_responses_stream_completes() -> None:
    m = MockModel()
    body = json.dumps({"model": "x", "stream": True, "input": "ok"}).encode("utf-8")
    resp = await m.respond(Protocol.RESPONSES, body, stream=True)
    raw = await _drain_stream(resp)

    for tag in ("response.created", "response.output_text.delta", "response.completed"):
        assert tag in raw, f"stream missing event {tag!r}"


# ---------- 错误路径 ----------


async def test_invalid_json_raises_service_error() -> None:
    m = MockModel()
    with pytest.raises(ServiceError) as exc:
        await m.respond(Protocol.MESSAGES, b"not a json", stream=False)
    assert exc.value.status == 400
    assert exc.value.code == "invalid_json_body"


async def test_json_non_object_raises() -> None:
    m = MockModel()
    with pytest.raises(ServiceError) as exc:
        await m.respond(Protocol.MESSAGES, b"[1, 2, 3]", stream=False)
    assert exc.value.status == 400
    assert exc.value.code == "invalid_json_body"


async def test_empty_messages_still_returns_placeholder() -> None:
    """空消息不炸;MockModel 返占位 echo("收到空消息")。"""
    m = MockModel()
    body = json.dumps({"model": "x", "messages": []}).encode("utf-8")
    resp = await m.respond(Protocol.CHAT_COMPLETIONS, body, stream=False)
    data = json.loads(bytes(resp.body))
    assert "mock" in data["choices"][0]["message"]["content"].lower()

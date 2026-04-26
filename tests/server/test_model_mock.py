"""MockModel 测试 —— Anthropic Messages echo 响应契约(结构 + 文本 + SSE)。"""

from __future__ import annotations

import json

import pytest
from fastapi.responses import Response, StreamingResponse

from chariot.server.model.mock import MockModel
from chariot.server.service.exceptions import ServiceError


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
    resp = await m.respond(body, stream=False)
    data = json.loads(bytes(resp.body))

    assert data["type"] == "message"
    assert data["role"] == "assistant"
    assert data["stop_reason"] == "end_turn"
    assert data["content"][0]["type"] == "text"
    assert data["content"][0]["text"].startswith("[mock echo]")
    assert data["content"][0]["text"].endswith("hi")
    assert data["model"] == "mock-echo-v1"


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
    resp = await m.respond(body, stream=True)
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
    # 文本被 chunk 成 4 字符一段;首个 delta 带 "[mo" 或 "mo" 这种前缀
    assert '"text": "[mo' in raw or '"text": "mo' in raw
    assert "hello"[:3] in raw


# ---------- 错误路径 ----------


async def test_invalid_json_raises_service_error() -> None:
    m = MockModel()
    with pytest.raises(ServiceError) as exc:
        await m.respond(b"not a json", stream=False)
    assert exc.value.status == 400
    assert exc.value.code == "invalid_json_body"


async def test_json_non_object_raises() -> None:
    m = MockModel()
    with pytest.raises(ServiceError) as exc:
        await m.respond(b"[1, 2, 3]", stream=False)
    assert exc.value.status == 400
    assert exc.value.code == "invalid_json_body"


async def test_empty_messages_still_returns_placeholder() -> None:
    """空消息不炸;MockModel 返占位 echo("收到空消息")。"""
    m = MockModel()
    body = json.dumps({"model": "x", "messages": []}).encode("utf-8")
    resp = await m.respond(body, stream=False)
    data = json.loads(bytes(resp.body))
    assert "mock" in data["content"][0]["text"].lower()

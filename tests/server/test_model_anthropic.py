"""AnthropicModel 测试 —— from_config 校验 + 非流 + 流式路径。

用 `httpx.MockTransport` 拦截请求,不真打 anthropic API。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable

import httpx
import pytest
from fastapi.responses import StreamingResponse

from chariot.agent.config import ConfigError
from chariot.server.model.anthropic import AnthropicModel
from chariot.server.service.exceptions import ServiceError


async def _drain_streaming_response(resp: StreamingResponse) -> bytes:
    chunks: list[bytes] = []
    async for c in resp.body_iterator:
        if isinstance(c, bytes):
            chunks.append(c)
        elif isinstance(c, str):
            chunks.append(c.encode("utf-8"))
        else:
            chunks.append(bytes(c))
    return b"".join(chunks)


def _build_model(
    *,
    handler: Callable[[httpx.Request], httpx.Response],
    model: str = "claude-haiku-4-5",
) -> AnthropicModel:
    """构造一个绑定 MockTransport 的 AnthropicModel,handler 自定义上游响应。"""
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(
        transport=transport,
        base_url="http://upstream.test",
        headers={"x-api-key": "fake", "anthropic-version": "2023-06-01"},
    )
    return AnthropicModel(api_key="fake", model=model, client=client)


# ---------- from_config 校验 ----------


def test_from_config_minimal_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    m = AnthropicModel.from_config({"model": "claude-opus-4-5"})
    assert isinstance(m, AnthropicModel)
    assert m.name == "anthropic"


def test_from_config_custom_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_KEY", "sk-x")
    m = AnthropicModel.from_config({"model": "claude-opus-4-5", "api_key_env": "MY_KEY"})
    assert isinstance(m, AnthropicModel)


def test_from_config_missing_model_raises() -> None:
    with pytest.raises(ConfigError, match="model"):
        AnthropicModel.from_config({})


def test_from_config_missing_api_key_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        AnthropicModel.from_config({"model": "claude-opus-4-5"})


def test_from_config_missing_custom_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_KEY", raising=False)
    with pytest.raises(ConfigError, match="MISSING_KEY"):
        AnthropicModel.from_config({"model": "x", "api_key_env": "MISSING_KEY"})


def test_from_config_inline_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """直接在 config 写 api_key,不依赖 env。"""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    m = AnthropicModel.from_config({"model": "claude-opus-4-5", "api_key": "sk-inline"})
    assert isinstance(m, AnthropicModel)


def test_from_config_inline_api_key_takes_priority_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两者都给时 inline api_key 优先(env 应被忽略)。"""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
    m = AnthropicModel.from_config({"model": "claude-opus-4-5", "api_key": "sk-from-config"})
    # 验证落到 httpx 客户端 header 的 key 是 inline 那条,不是 env 那条
    assert m._client.headers["x-api-key"] == "sk-from-config"


def test_from_config_empty_inline_api_key_raises() -> None:
    with pytest.raises(ConfigError, match="api_key"):
        AnthropicModel.from_config({"model": "x", "api_key": ""})


def test_from_config_non_string_inline_api_key_raises() -> None:
    with pytest.raises(ConfigError, match="api_key"):
        AnthropicModel.from_config({"model": "x", "api_key": 12345})  # type: ignore[dict-item]


# ---------- model 改写 ----------


async def test_respond_rewrites_model() -> None:
    captured: dict[str, bytes] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = req.content
        return httpx.Response(
            200,
            json={
                "id": "msg_x",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "hi"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    m = _build_model(handler=handler, model="claude-opus-4-5")
    body = json.dumps({"model": "client-wrote-this", "messages": []}).encode("utf-8")
    resp = await m.respond(body, stream=False)

    assert resp.status_code == 200
    assert "body" in captured
    sent = json.loads(captured["body"])
    assert sent["model"] == "claude-opus-4-5"  # 改写了
    assert sent["messages"] == []  # 其它字段保留


async def test_respond_invalid_json_body_400() -> None:
    m = _build_model(handler=lambda _: httpx.Response(200, json={}))
    with pytest.raises(ServiceError) as exc:
        await m.respond(b"not json", stream=False)
    assert exc.value.status == 400


async def test_respond_non_object_body_400() -> None:
    m = _build_model(handler=lambda _: httpx.Response(200, json={}))
    with pytest.raises(ServiceError) as exc:
        await m.respond(b"[1, 2]", stream=False)
    assert exc.value.status == 400


# ---------- 上游响应映射 ----------


async def test_respond_2xx_passthrough() -> None:
    upstream_body = b'{"id":"msg_y","type":"message","content":[{"type":"text","text":"ok"}]}'

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=upstream_body, headers={"content-type": "application/json"}
        )

    m = _build_model(handler=handler)
    body = json.dumps({"model": "x", "messages": []}).encode("utf-8")
    resp = await m.respond(body, stream=False)

    assert resp.status_code == 200
    assert bytes(resp.body) == upstream_body
    assert resp.media_type == "application/json"


async def test_respond_429_passthrough() -> None:
    """rate limit 透传给客户端,不重映射。"""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"type": "rate_limit"}})

    m = _build_model(handler=handler)
    body = json.dumps({"model": "x", "messages": []}).encode("utf-8")
    resp = await m.respond(body, stream=False)

    assert resp.status_code == 429


async def test_respond_other_4xx_passthrough() -> None:
    """非 401/403/429 的 4xx 透传(让客户端拿到原始错误体)。"""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"type": "invalid_request"}})

    m = _build_model(handler=handler)
    body = json.dumps({"model": "x", "messages": []}).encode("utf-8")
    resp = await m.respond(body, stream=False)

    assert resp.status_code == 400


async def test_respond_401_maps_to_502() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"type": "auth_error"}})

    m = _build_model(handler=handler)
    body = json.dumps({"model": "x", "messages": []}).encode("utf-8")
    with pytest.raises(ServiceError) as exc:
        await m.respond(body, stream=False)
    assert exc.value.status == 502
    assert exc.value.code == "upstream_auth_failed"


async def test_respond_403_maps_to_502() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={})

    m = _build_model(handler=handler)
    body = json.dumps({"model": "x", "messages": []}).encode("utf-8")
    with pytest.raises(ServiceError) as exc:
        await m.respond(body, stream=False)
    assert exc.value.status == 502


async def test_respond_500_maps_to_502() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"upstream blew up")

    m = _build_model(handler=handler)
    body = json.dumps({"model": "x", "messages": []}).encode("utf-8")
    with pytest.raises(ServiceError) as exc:
        await m.respond(body, stream=False)
    assert exc.value.status == 502
    assert exc.value.code == "upstream_server_error"


async def test_respond_503_maps_to_502() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"unavailable")

    m = _build_model(handler=handler)
    body = json.dumps({"model": "x", "messages": []}).encode("utf-8")
    with pytest.raises(ServiceError) as exc:
        await m.respond(body, stream=False)
    assert exc.value.status == 502


async def test_respond_network_error_maps_to_502() -> None:
    """httpx 抛网络异常(连接拒绝 / DNS 失败等)→ 502。"""

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated DNS failure")

    m = _build_model(handler=handler)
    body = json.dumps({"model": "x", "messages": []}).encode("utf-8")
    with pytest.raises(ServiceError) as exc:
        await m.respond(body, stream=False)
    assert exc.value.status == 502
    assert exc.value.code == "upstream_unreachable"


async def test_respond_timeout_maps_to_502() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated timeout")

    m = _build_model(handler=handler)
    body = json.dumps({"model": "x", "messages": []}).encode("utf-8")
    with pytest.raises(ServiceError) as exc:
        await m.respond(body, stream=False)
    assert exc.value.status == 502
    assert exc.value.code == "upstream_timeout"


# ---------- 流式路径 ----------


def _sse_handler(chunks: list[bytes]) -> Callable[[httpx.Request], httpx.Response]:
    """构造一个返 SSE 流的 mock handler。"""

    def handler(req: httpx.Request) -> httpx.Response:
        async def stream_body() -> AsyncIterator[bytes]:
            for c in chunks:
                yield c

        return httpx.Response(
            200,
            content=stream_body(),
            headers={"content-type": "text/event-stream"},
        )

    return handler


async def test_respond_stream_2xx_forwards_chunks() -> None:
    delta_payload = b'{"type":"content_block_delta","delta":{"text":"hi"}}'
    chunks = [
        b'event: message_start\ndata: {"type":"message_start"}\n\n',
        b"event: content_block_delta\ndata: " + delta_payload + b"\n\n",
        b"event: message_stop\ndata: {}\n\n",
    ]
    m = _build_model(handler=_sse_handler(chunks))
    body = json.dumps({"model": "x", "stream": True, "messages": []}).encode("utf-8")
    resp = await m.respond(body, stream=True)

    assert isinstance(resp, StreamingResponse)
    assert resp.status_code == 200
    assert resp.media_type == "text/event-stream"
    raw = await _drain_streaming_response(resp)
    assert b"message_start" in raw
    assert b"content_block_delta" in raw
    assert b"message_stop" in raw


async def test_respond_stream_rewrites_model() -> None:
    captured: dict[str, bytes] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = req.content

        async def stream_body() -> AsyncIterator[bytes]:
            yield b"event: message_start\ndata: {}\n\n"

        return httpx.Response(
            200, content=stream_body(), headers={"content-type": "text/event-stream"}
        )

    m = _build_model(handler=handler, model="claude-opus-4-5")
    body = json.dumps({"model": "client-x", "stream": True, "messages": []}).encode("utf-8")
    resp = await m.respond(body, stream=True)
    # 必须把流消费掉,handler 才会被调
    await _drain_streaming_response(resp)  # type: ignore[arg-type]
    sent = json.loads(captured["body"])
    assert sent["model"] == "claude-opus-4-5"


async def test_respond_stream_401_maps_to_502_before_first_byte() -> None:
    """首响应 401 还没发字节给客户端 → 可正常映射成 502。"""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"type": "auth_error"}})

    m = _build_model(handler=handler)
    body = json.dumps({"model": "x", "stream": True, "messages": []}).encode("utf-8")
    with pytest.raises(ServiceError) as exc:
        await m.respond(body, stream=True)
    assert exc.value.status == 502
    assert exc.value.code == "upstream_auth_failed"


async def test_respond_stream_500_maps_to_502_before_first_byte() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"oops")

    m = _build_model(handler=handler)
    body = json.dumps({"model": "x", "stream": True, "messages": []}).encode("utf-8")
    with pytest.raises(ServiceError) as exc:
        await m.respond(body, stream=True)
    assert exc.value.status == 502


async def test_respond_stream_429_passthrough_before_first_byte() -> None:
    """流式情形下 429 也透传(还没开始 SSE,可以正常返非流响应)。"""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"type": "rate_limit"}})

    m = _build_model(handler=handler)
    body = json.dumps({"model": "x", "stream": True, "messages": []}).encode("utf-8")
    resp = await m.respond(body, stream=True)
    assert resp.status_code == 429


async def test_respond_stream_network_error_maps_to_502() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated DNS failure")

    m = _build_model(handler=handler)
    body = json.dumps({"model": "x", "stream": True, "messages": []}).encode("utf-8")
    with pytest.raises(ServiceError) as exc:
        await m.respond(body, stream=True)
    assert exc.value.status == 502
    assert exc.value.code == "upstream_unreachable"

"""OpenAIProvider 单测。

用 `httpx.MockTransport` 喂 fixture SSE bytes,断 yield 出的 ChatEvent
形态。
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest, Message
from chariot.agent.exceptions import ConfigError, ProviderError
from chariot.providers.builtin.openai import OpenAIProvider
from chariot.providers.clients import ClientCache


def _make_provider(
    handler: Callable[[httpx.Request], httpx.Response],
    monkeypatch: pytest.MonkeyPatch,
) -> OpenAIProvider:
    """构造 OpenAIProvider 实例,monkeypatch ClientCache.get。"""
    transport = httpx.MockTransport(handler)
    mock_client = httpx.AsyncClient(
        base_url="https://api.test",
        transport=transport,
        headers={
            "authorization": "Bearer test_key",
            "content-type": "application/json",
        },
    )

    async def _fake_get(spec: object) -> httpx.AsyncClient:
        del spec
        return mock_client

    monkeypatch.setattr(ClientCache, "get", _fake_get)

    return OpenAIProvider.create(
        {
            "model": "gpt-4",
            "api_key": "test_key",
            "base_url": "https://api.test",
        }
    )


def _make_req() -> ChatRequest:
    return ChatRequest(
        provider_name="openai",
        messages=[Message(role="user", content="hi")],
    )


class TestCaseATextOnly:
    """纯 text 响应。"""

    async def test_kind_sequence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sse_bytes = (
            b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
            b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
            b'data: {"choices":[{"delta":{"content":" world"}}]}\n\n'
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
            b"data: [DONE]\n\n"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=sse_bytes, headers={"content-type": "text/event-stream"})

        provider = _make_provider(handler, monkeypatch)
        events = [ev async for ev in provider.generate(_make_req())]
        kinds = [ev.kind for ev in events]
        assert kinds == [
            "message_start",
            "content_block_start",
            "content_block_delta",
            "content_block_delta",
            "message_delta",
            "content_block_stop",
            "message_stop",
        ]


class TestCaseBToolCalls:
    """tool_calls 响应。"""

    async def test_tool_call_sequence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sse_bytes = (
            b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_01","type":"function","function":{"name":"read_file","arguments":""}}]}}]}\n\n'
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"path\\": \\"/tmp\\"}"}}]}}]}\n\n'
            b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
            b"data: [DONE]\n\n"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=sse_bytes, headers={"content-type": "text/event-stream"})

        provider = _make_provider(handler, monkeypatch)
        events = [ev async for ev in provider.generate(_make_req())]
        kinds = [ev.kind for ev in events]
        assert kinds == [
            "message_start",
            "content_block_start",
            "content_block_delta",
            "message_delta",
            "content_block_stop",
            "message_stop",
        ]


class TestCaseC401:
    async def test_raise_auth_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": {"message": "Invalid API key"}})

        provider = _make_provider(handler, monkeypatch)
        with pytest.raises(ProviderError) as exc_info:
            async for _ in provider.generate(_make_req()):
                pass
        assert exc_info.value.code == "upstream_auth_failed"


class TestCaseD500:
    async def test_raise_server_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": {"message": "Internal error"}})

        provider = _make_provider(handler, monkeypatch)
        with pytest.raises(ProviderError) as exc_info:
            async for _ in provider.generate(_make_req()):
                pass
        assert exc_info.value.code == "upstream_server_error"


class TestCaseEUnreachable:
    async def test_raise_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        provider = _make_provider(handler, monkeypatch)
        with pytest.raises(ProviderError) as exc_info:
            async for _ in provider.generate(_make_req()):
                pass
        assert exc_info.value.code == "upstream_unreachable"


class TestCaseGCreate:
    def test_missing_model_raises_config_error(self) -> None:
        with pytest.raises(ConfigError):
            OpenAIProvider.create({})

    def test_empty_model_raises_config_error(self) -> None:
        with pytest.raises(ConfigError):
            OpenAIProvider.create({"model": ""})

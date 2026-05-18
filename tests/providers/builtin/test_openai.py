"""OpenAIProvider 单测。

用 `httpx.MockTransport` 喂 fixture SSE bytes,断 yield 出的 ChatEvent
形态。
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

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
        provider_ref="openai",
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

    def test_build_body_preserves_tool_result_messages(self) -> None:
        provider = OpenAIProvider.create({"model": "gpt-4", "api_key": "test_key"})
        req = ChatRequest(
            provider_ref="provider-slug",
            messages=[
                Message(
                    role="assistant",
                    content=[
                        {"type": "text", "text": "calling tool"},
                        {
                            "type": "tool_use",
                            "id": "call_01",
                            "name": "read_file",
                            "input": {"path": "/tmp/demo.txt"},
                        },
                    ],
                ),
                Message(
                    role="user",
                    content=[
                        {"type": "tool_result", "tool_use_id": "call_01", "content": [{"type": "text", "text": "ok"}]},
                        {"type": "text", "text": "next step"},
                    ],
                ),
            ],
        )

        body = provider._build_body(req)

        assert "provider_ref" not in body
        assert body["messages"] == [
            {
                "role": "assistant",
                "content": "calling tool",
                "tool_calls": [
                    {
                        "id": "call_01",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"path": "/tmp/demo.txt"}),
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_01", "content": "ok"},
            {"role": "user", "content": "next step"},
        ]

    def test_frame_to_event_supports_multiple_tool_calls(self) -> None:
        events = OpenAIProvider._frame_to_event(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_01",
                                    "function": {"name": "read_file", "arguments": '{"path":"a"'},
                                },
                                {
                                    "index": 1,
                                    "id": "call_02",
                                    "function": {"name": "list_dir", "arguments": '{"path":"b"'},
                                },
                            ]
                        }
                    }
                ]
            },
            open_blocks=set(),
        )

        assert [(event.kind, event.index) for event in events] == [
            ("content_block_start", 1),
            ("content_block_delta", 1),
            ("content_block_start", 2),
            ("content_block_delta", 2),
        ]
        assert events[0].content_block == {
            "type": "tool_use",
            "id": "call_01",
            "name": "read_file",
            "input": {},
        }
        assert events[2].content_block == {
            "type": "tool_use",
            "id": "call_02",
            "name": "list_dir",
            "input": {},
        }


class _BrokenStream(httpx.AsyncByteStream):
    async def __aiter__(self) -> object:
        yield b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
        raise httpx.ReadError("boom")

    async def aclose(self) -> None:
        return None


class TestCaseFStreamInterrupted:
    async def test_yields_error_event_after_stream_started(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                stream=_BrokenStream(),
                headers={"content-type": "text/event-stream"},
            )

        provider = _make_provider(handler, monkeypatch)
        events = [ev async for ev in provider.generate(_make_req())]

        assert [ev.kind for ev in events] == ["message_start", "error"]
        assert events[-1].error_type == "upstream_stream_error"


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

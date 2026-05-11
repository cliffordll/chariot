"""AnthropicProvider 单测。

用 `httpx.MockTransport` 喂 fixture SSE bytes,断 yield 出的 ChatEvent
形态。覆盖 6 个 case:

- A 纯 text 响应:序列形态符合 Claude(message_start → content_block_* →
  message_delta → message_stop)
- B tool_use 流:含 tool_use 形态的 content_block_start /
  input_json_delta / content_block_stop
- C 401:抛 ProviderError(code="upstream_auth_failed")
- D 500:抛 ProviderError(code="upstream_server_error")
- E httpx.ConnectError:抛 ProviderError(code="upstream_unreachable")
- F 200 已发后中途 IO 错:yield ChatEvent(kind="error",
  error_type="upstream_stream_error"),**不抛**

`create` 配置错路径单独覆盖(`ConfigError` 子类)。
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest, Message
from chariot.agent.exceptions import ConfigError, ProviderError
from chariot.providers.builtin.anthropic import AnthropicProvider
from chariot.providers.clients import ClientCache

# ---------------------------------------------------------------------------
# Fixture SSE bytes(模拟真实 Anthropic SSE 响应)
# ---------------------------------------------------------------------------


SSE_TEXT_ONLY: bytes = (
    b"event: message_start\n"
    b'data: {"type":"message_start","message":{"id":"msg_01","type":"message",'
    b'"role":"assistant","content":[],"model":"claude-3-5-sonnet-20241022",'
    b'"stop_reason":null,"stop_sequence":null,'
    b'"usage":{"input_tokens":10,"output_tokens":1}}}\n\n'
    b"event: content_block_start\n"
    b'data: {"type":"content_block_start","index":0,'
    b'"content_block":{"type":"text","text":""}}\n\n'
    b"event: content_block_delta\n"
    b'data: {"type":"content_block_delta","index":0,'
    b'"delta":{"type":"text_delta","text":"Hello"}}\n\n'
    b"event: content_block_delta\n"
    b'data: {"type":"content_block_delta","index":0,'
    b'"delta":{"type":"text_delta","text":" world"}}\n\n'
    b"event: content_block_stop\n"
    b'data: {"type":"content_block_stop","index":0}\n\n'
    b"event: message_delta\n"
    b'data: {"type":"message_delta",'
    b'"delta":{"stop_reason":"end_turn","stop_sequence":null},'
    b'"usage":{"output_tokens":2}}\n\n'
    b"event: message_stop\n"
    b'data: {"type":"message_stop"}\n\n'
)


SSE_TOOL_USE: bytes = (
    b"event: message_start\n"
    b'data: {"type":"message_start","message":{"id":"msg_02","type":"message",'
    b'"role":"assistant","content":[],"model":"claude-3-5-sonnet-20241022",'
    b'"stop_reason":null,"stop_sequence":null,'
    b'"usage":{"input_tokens":20,"output_tokens":1}}}\n\n'
    b"event: content_block_start\n"
    b'data: {"type":"content_block_start","index":0,'
    b'"content_block":{"type":"tool_use","id":"toolu_01","name":"read_file","input":{}}}\n\n'
    b"event: content_block_delta\n"
    b'data: {"type":"content_block_delta","index":0,'
    b'"delta":{"type":"input_json_delta","partial_json":"{\\"path\\":"}}\n\n'
    b"event: content_block_delta\n"
    b'data: {"type":"content_block_delta","index":0,'
    b'"delta":{"type":"input_json_delta","partial_json":"\\"/tmp\\"}"}}\n\n'
    b"event: content_block_stop\n"
    b'data: {"type":"content_block_stop","index":0}\n\n'
    b"event: message_delta\n"
    b'data: {"type":"message_delta",'
    b'"delta":{"stop_reason":"tool_use","stop_sequence":null},'
    b'"usage":{"output_tokens":15}}\n\n'
    b"event: message_stop\n"
    b'data: {"type":"message_stop"}\n\n'
)


# ---------------------------------------------------------------------------
# Test fixtures(构造 Provider 实例)
# ---------------------------------------------------------------------------


def _make_provider(
    handler: Callable[[httpx.Request], httpx.Response],
    monkeypatch: pytest.MonkeyPatch,
) -> AnthropicProvider:
    """构造一个 AnthropicProvider 实例,顺带 monkeypatch ClientCache.get 返回挂
    MockTransport 的 mock client。

    0.6.5 起 Provider 不持 client,改 monkeypatch ClientCache.get 注入 mock。
    """
    transport = httpx.MockTransport(handler)
    mock_client = httpx.AsyncClient(
        base_url="https://api.test",
        transport=transport,
        headers={
            "x-api-key": "test_key",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )

    async def _fake_get(spec: object) -> httpx.AsyncClient:
        del spec  # 测试场景忽略 spec,所有 generate 都走同一个 mock client
        return mock_client

    monkeypatch.setattr(ClientCache, "get", _fake_get)

    return AnthropicProvider.create(
        {
            "model": "claude-test",
            "api_key": "test_key",
            "base_url": "https://api.test",
        }
    )


def _make_req() -> ChatRequest:
    return ChatRequest(
        provider_name="anthropic",
        messages=[Message(role="user", content="hi")],
    )


# ---------------------------------------------------------------------------
# Case A:纯 text 响应
# ---------------------------------------------------------------------------


class TestCaseATextOnly:
    """纯 text 响应的 ChatEvent 序列形态。"""

    async def test_kind_sequence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=SSE_TEXT_ONLY,
                headers={"content-type": "text/event-stream"},
            )

        provider = _make_provider(handler, monkeypatch)
        events = [ev async for ev in provider.generate(_make_req())]
        kinds = [ev.kind for ev in events]
        assert kinds == [
            "message_start",
            "content_block_start",
            "content_block_delta",
            "content_block_delta",
            "content_block_stop",
            "message_delta",
            "message_stop",
        ]

    async def test_text_delta_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=SSE_TEXT_ONLY,
                headers={"content-type": "text/event-stream"},
            )

        provider = _make_provider(handler, monkeypatch)
        events = [ev async for ev in provider.generate(_make_req())]
        text_deltas = [ev for ev in events if ev.kind == "content_block_delta"]
        assert len(text_deltas) == 2
        assert text_deltas[0].delta == {"type": "text_delta", "text": "Hello"}
        assert text_deltas[1].delta == {"type": "text_delta", "text": " world"}

    async def test_message_start_has_usage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=SSE_TEXT_ONLY,
                headers={"content-type": "text/event-stream"},
            )

        provider = _make_provider(handler, monkeypatch)
        events = [ev async for ev in provider.generate(_make_req())]
        ms = events[0]
        assert ms.kind == "message_start"
        assert ms.usage == {"input_tokens": 10, "output_tokens": 1}

    async def test_message_delta_stop_reason(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=SSE_TEXT_ONLY,
                headers={"content-type": "text/event-stream"},
            )

        provider = _make_provider(handler, monkeypatch)
        events = [ev async for ev in provider.generate(_make_req())]
        md = next(ev for ev in events if ev.kind == "message_delta")
        assert md.delta == {"stop_reason": "end_turn", "stop_sequence": None}
        assert md.usage == {"output_tokens": 2}


# ---------------------------------------------------------------------------
# Case B:tool_use 流
# ---------------------------------------------------------------------------


class TestCaseBToolUse:
    async def test_tool_use_block_start(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=SSE_TOOL_USE,
                headers={"content-type": "text/event-stream"},
            )

        provider = _make_provider(handler, monkeypatch)
        events = [ev async for ev in provider.generate(_make_req())]
        cb_start = next(ev for ev in events if ev.kind == "content_block_start")
        assert cb_start.content_block is not None
        assert cb_start.content_block["type"] == "tool_use"
        assert cb_start.content_block["id"] == "toolu_01"
        assert cb_start.content_block["name"] == "read_file"

    async def test_input_json_delta(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=SSE_TOOL_USE,
                headers={"content-type": "text/event-stream"},
            )

        provider = _make_provider(handler, monkeypatch)
        events = [ev async for ev in provider.generate(_make_req())]
        deltas = [ev for ev in events if ev.kind == "content_block_delta"]
        assert len(deltas) == 2
        for d in deltas:
            assert d.delta is not None
            assert d.delta["type"] == "input_json_delta"

    async def test_message_delta_stop_reason_tool_use(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=SSE_TOOL_USE,
                headers={"content-type": "text/event-stream"},
            )

        provider = _make_provider(handler, monkeypatch)
        events = [ev async for ev in provider.generate(_make_req())]
        md = next(ev for ev in events if ev.kind == "message_delta")
        assert md.delta == {"stop_reason": "tool_use", "stop_sequence": None}


# ---------------------------------------------------------------------------
# Case C:401 → upstream_auth_failed
# ---------------------------------------------------------------------------


class TestCaseC401AuthFailed:
    async def test_raises_provider_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401,
                json={"error": {"type": "authentication_error", "message": "invalid key"}},
            )

        provider = _make_provider(handler, monkeypatch)
        with pytest.raises(ProviderError) as exc_info:
            async for _ in provider.generate(_make_req()):
                pass
        assert exc_info.value.code == "upstream_auth_failed"

    async def test_403_also_auth_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"error": {"type": "permission_error"}})

        provider = _make_provider(handler, monkeypatch)
        with pytest.raises(ProviderError) as exc_info:
            async for _ in provider.generate(_make_req()):
                pass
        assert exc_info.value.code == "upstream_auth_failed"


# ---------------------------------------------------------------------------
# Case D:500 → upstream_server_error
# ---------------------------------------------------------------------------


class TestCaseD500ServerError:
    async def test_raises_provider_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": {"type": "internal_error"}})

        provider = _make_provider(handler, monkeypatch)
        with pytest.raises(ProviderError) as exc_info:
            async for _ in provider.generate(_make_req()):
                pass
        assert exc_info.value.code == "upstream_server_error"

    async def test_503_also_server_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"error": {"type": "overloaded_error"}})

        provider = _make_provider(handler, monkeypatch)
        with pytest.raises(ProviderError) as exc_info:
            async for _ in provider.generate(_make_req()):
                pass
        assert exc_info.value.code == "upstream_server_error"


# ---------------------------------------------------------------------------
# Case E:httpx.ConnectError → upstream_unreachable
# ---------------------------------------------------------------------------


class TestCaseEConnectError:
    async def test_raises_provider_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        provider = _make_provider(handler, monkeypatch)
        with pytest.raises(ProviderError) as exc_info:
            async for _ in provider.generate(_make_req()):
                pass
        assert exc_info.value.code == "upstream_unreachable"

    async def test_timeout_also_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("upstream slow")

        provider = _make_provider(handler, monkeypatch)
        with pytest.raises(ProviderError) as exc_info:
            async for _ in provider.generate(_make_req()):
                pass
        assert exc_info.value.code == "upstream_unreachable"


# ---------------------------------------------------------------------------
# Case F:200 已发后中途 IO 错 → yield error event,不抛
# ---------------------------------------------------------------------------


class TestCaseF200ThenStreamError:
    """SSE 流中途 IO 错 → yield ChatEvent(kind="error") + return,不抛。"""

    async def test_stream_error_yields_event(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """200 已发后中途 IO 错 → yield error event,不抛。"""

        class _FailingStream(httpx.AsyncByteStream):
            """先 yield 一个完整 message_start 帧,再抛 ReadError。"""

            _CHUNK = (
                b"event: message_start\n"
                b'data: {"type":"message_start","message":{"id":"msg_x",'
                b'"type":"message","role":"assistant","content":[],'
                b'"model":"claude-test","stop_reason":null,"stop_sequence":null,'
                b'"usage":{"input_tokens":1,"output_tokens":1}}}\n\n'
            )

            async def __aiter__(self):  # type: ignore[override]
                yield self._CHUNK
                raise httpx.ReadError("simulated mid-stream error")

            async def aclose(self) -> None:
                return None

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                stream=_FailingStream(),
                headers={"content-type": "text/event-stream"},
            )

        provider = _make_provider(handler, monkeypatch)
        events = [ev async for ev in provider.generate(_make_req())]
        # 必须有 message_start(在错误前 yield 出来)
        assert events[0].kind == "message_start"
        # 末尾必须是 error event,且 error_type 是 upstream_stream_error
        assert events[-1].kind == "error"
        assert events[-1].error_type == "upstream_stream_error"


# ---------------------------------------------------------------------------
# create 配置校验
# ---------------------------------------------------------------------------


class TestCreate:
    def test_missing_model_raises_config_error(self) -> None:
        with pytest.raises(ConfigError, match="model"):
            AnthropicProvider.create({"api_key": "x"})

    def test_inline_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 防意外 fallback 到 env
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        provider = AnthropicProvider.create({"model": "claude-3-5-sonnet-20241022", "api_key": "sk-ant-xxx"})
        assert provider.config.model == "claude-3-5-sonnet-20241022"

    def test_env_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-yyy")
        provider = AnthropicProvider.create({"model": "claude-3-5-sonnet-20241022"})
        assert provider.config.model == "claude-3-5-sonnet-20241022"

    def test_missing_api_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(ConfigError, match="api_key"):
            AnthropicProvider.create({"model": "claude-3-5-sonnet-20241022"})

    def test_custom_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        provider = AnthropicProvider.create({"model": "claude-3-5-sonnet-20241022", "base_url": "https://custom.api"})
        # base_url 在 _build_body 不暴露,只能间接验:_spec.base_url 字段
        assert provider._spec.base_url == "https://custom.api"

    def test_base_url_falls_back_to_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """S.7.3 起:options 无 inline base_url → 读 ANTHROPIC_BASE_URL env(Anthropic SDK 约定)。"""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://env.api")
        provider = AnthropicProvider.create({"model": "claude-3-5-sonnet-20241022"})
        assert provider._spec.base_url == "https://env.api"

    def test_inline_base_url_wins_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """inline > env(CLI flag 通过 inline 注入,所以 CLI > env;DB 显式 inline 也 > env)。"""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://env.api")
        provider = AnthropicProvider.create({"model": "claude-3-5-sonnet-20241022", "base_url": "https://inline.api"})
        assert provider._spec.base_url == "https://inline.api"

    def test_base_url_default_when_no_inline_no_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """都没设 → 默认 https://api.anthropic.com。"""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
        provider = AnthropicProvider.create({"model": "claude-3-5-sonnet-20241022"})
        assert provider._spec.base_url == "https://api.anthropic.com"

    def test_empty_inline_base_url_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """inline base_url 是空串 → 校验失败(让用户改成"整条删掉")。"""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        with pytest.raises(ConfigError, match="base_url"):
            AnthropicProvider.create({"model": "claude-3-5-sonnet-20241022", "base_url": ""})


# ---------------------------------------------------------------------------
# _build_body:Claude API 请求拼装
# ---------------------------------------------------------------------------


class TestBuildBody:
    @staticmethod
    def _provider() -> AnthropicProvider:
        """provider 实例;_build_body 不需要 client,直接 create 构造。"""
        return AnthropicProvider.create(
            {
                "model": "claude-test",
                "api_key": "test_key",
                "base_url": "https://api.test",
            }
        )

    def test_excludes_chariot_extension_fields(self) -> None:
        req = ChatRequest(
            provider_name="anthropic",
            messages=[Message(role="user", content="hi")],
            conversation_id="01H...",
            agent_id="agent_main",
        )
        body = self._provider()._build_body(req)
        assert "conversation_id" not in body
        assert "agent_id" not in body

    def test_excludes_none_fields(self) -> None:
        req = ChatRequest(
            provider_name="anthropic",
            messages=[Message(role="user", content="hi")],
        )
        body = self._provider()._build_body(req)
        # temperature / top_p / 等默认 None 字段应剔除
        assert "temperature" not in body
        assert "top_p" not in body
        assert "tool_choice" not in body
        assert "metadata" not in body

    def test_forces_stream_true(self) -> None:
        req = ChatRequest(
            provider_name="anthropic",
            messages=[Message(role="user", content="hi")],
        )
        body = self._provider()._build_body(req)
        assert body["stream"] is True

    def test_keeps_messages_and_max_tokens(self) -> None:
        req = ChatRequest(
            provider_name="anthropic",
            messages=[Message(role="user", content="hi")],
            max_tokens=2048,
        )
        body = self._provider()._build_body(req)
        assert body["max_tokens"] == 2048
        assert isinstance(body["messages"], list)

    def test_keeps_explicit_temperature(self) -> None:
        req = ChatRequest(
            provider_name="anthropic",
            messages=[Message(role="user", content="hi")],
            temperature=0.5,
        )
        body = self._provider()._build_body(req)
        assert body["temperature"] == 0.5

    def test_writes_body_model_from_config_model(self) -> None:
        """body.model 必须从 self.config.model(LLM 真实 id)写入,跟
        req.provider_name(chariot 路由的 entry name)无关。漏掉这步上游报
        not_found_error。
        """
        req = ChatRequest(
            provider_name="ollama-qwen",  # chariot entry name
            messages=[Message(role="user", content="hi")],
        )
        # _make_provider 默认 config.model="claude-test"
        body = self._provider()._build_body(req)
        assert body["model"] == "claude-test"
        # provider_name 不进 body
        assert "provider_name" not in body
        assert "ollama-qwen" not in str(body)

    def test_per_call_model_override_takes_precedence(self) -> None:
        """0.6.5+:`req.model` 非 None 时优先于 `self.config.model`。

        CLI `--model` flag 走这条 —— per-call 覆盖,不重建 Provider /
        ClientSpec / httpx client(零客户端开销)。
        """
        req = ChatRequest(
            provider_name="anthropic",
            messages=[Message(role="user", content="hi")],
            model="claude-haiku-4-5",  # per-call 覆盖
        )
        # config.model 默认 "claude-test",req.model 应胜出
        body = self._provider()._build_body(req)
        assert body["model"] == "claude-haiku-4-5"

    def test_per_call_model_none_falls_back_to_config(self) -> None:
        """`req.model=None`(默认)→ 回退 self.config.model;不写 null。"""
        req = ChatRequest(
            provider_name="anthropic",
            messages=[Message(role="user", content="hi")],
            # model 默认 None
        )
        body = self._provider()._build_body(req)
        assert body["model"] == "claude-test"
        # None 字段已被滤;model 不应是 None
        assert body["model"] is not None


# ---------------------------------------------------------------------------
# _frame_to_event:SSE 帧 → ChatEvent 翻译
# ---------------------------------------------------------------------------


class TestFrameToEvent:
    def test_unknown_event_returns_none(self) -> None:
        ev = AnthropicProvider._frame_to_event(None, {"type": "future_event"})
        assert ev is None

    def test_no_type_returns_none(self) -> None:
        ev = AnthropicProvider._frame_to_event(None, {"foo": "bar"})
        assert ev is None

    def test_ping_event(self) -> None:
        ev = AnthropicProvider._frame_to_event("ping", {"type": "ping"})
        assert ev is not None
        assert ev.kind == "ping"

    def test_error_event_payload(self) -> None:
        ev = AnthropicProvider._frame_to_event(
            "error",
            {"type": "error", "error": {"type": "overloaded_error", "message": "too busy"}},
        )
        assert ev is not None
        assert ev.kind == "error"
        assert ev.error_type == "overloaded_error"
        assert ev.error_message == "too busy"

    def test_unused_chat_event_factory_smoke(self) -> None:
        """防 ChatEvent import 被裁剪。"""
        assert ChatEvent.message_done().kind == "message_stop"

"""ProviderProber 单测。

覆盖:
- success:provider 流给 message_stop → ProbeResult(ok=True, latency_ms>=0)
- yields error event:provider 流中 yield kind=error → ok=False,error.code 透传
- raises ProviderError:provider 抛 ProviderError(200 前)→ ok=False,e.code 透传
- ConfigError(ProviderRegistry.build 抛)→ ok=False,code='config_error',latency_ms=0
- incomplete_stream:provider 流自然结束没 message_stop / error → ok=False,
  code='incomplete_stream'
- 兜底 generic Exception → ok=False,code='probe_internal_error'
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Self

import pytest

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest
from chariot.agent.config import ModelEntry
from chariot.agent.exceptions import ProviderError
from chariot.providers.base import BaseProvider, BaseProviderConfig
from chariot.providers.prober import ProviderProber
from chariot.providers.registry import ProviderRegistry

# ---------------------------------------------------------------------------
# Mock providers — 各模拟一种探针场景
# ---------------------------------------------------------------------------


class _SuccessProvider(BaseProvider):
    """正常 streaming:吐 message_start → text → message_stop。"""

    def __init__(self) -> None:
        self.config = BaseProviderConfig(name="success", model="success-1")

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> Self:
        del options
        return cls()

    async def generate(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        del req
        yield ChatEvent.message_start(message_id="m1", model=self.config.model)
        yield ChatEvent.text_block_start(index=0)
        yield ChatEvent.text_delta("pong", index=0)
        yield ChatEvent.block_stop(index=0)
        yield ChatEvent.message_delta_done(stop_reason="end_turn")
        yield ChatEvent.message_done()


class _YieldsErrorProvider(BaseProvider):
    """200 后流中 yield error event(provider 内部 IO 错的契约形态)。"""

    def __init__(self) -> None:
        self.config = BaseProviderConfig(name="yield_err", model="yield_err-1")

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> Self:
        del options
        return cls()

    async def generate(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        del req
        yield ChatEvent.message_start(message_id="m1", model=self.config.model)
        yield ChatEvent.error_event(
            error_type="upstream_stream_error",
            error_message="simulated mid-stream IO error",
        )


class _RaisesProviderErrorProvider(BaseProvider):
    """200 前抛 ProviderError(典型如 401 / connect 失败)。"""

    def __init__(self) -> None:
        self.config = BaseProviderConfig(name="raises_pe", model="raises_pe-1")

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> Self:
        del options
        return cls()

    async def generate(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        del req
        raise ProviderError(
            code="upstream_auth_failed",
            message="401 Unauthorized",
        )
        yield  # type: ignore[unreachable]  # 让函数变 async generator


class _IncompleteStreamProvider(BaseProvider):
    """流自然结束但没 message_stop / error(异常但合规的边界)。"""

    def __init__(self) -> None:
        self.config = BaseProviderConfig(name="incomplete", model="incomplete-1")

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> Self:
        del options
        return cls()

    async def generate(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        del req
        yield ChatEvent.message_start(message_id="m1", model=self.config.model)
        yield ChatEvent.text_block_start(index=0)
        yield ChatEvent.text_delta("partial", index=0)
        # 没 message_stop 也没 error,流就这样断了


class _GenericExceptionProvider(BaseProvider):
    """generate 抛非 ProviderError 异常(ProviderProber 兜底分支)。"""

    def __init__(self) -> None:
        self.config = BaseProviderConfig(name="generic_exc", model="generic-1")

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> Self:
        del options
        return cls()

    async def generate(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        del req
        raise RuntimeError("totally unexpected boom")
        yield  # type: ignore[unreachable]


class _MissingApiKeyProvider(BaseProvider):
    """from_options 抛 ConfigError(模拟配置不全的 build 阶段失败)。"""

    def __init__(self) -> None:
        self.config = BaseProviderConfig(name="missing_key", model="missing-1")

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> Self:
        del options
        from chariot.agent.exceptions import ConfigError

        raise ConfigError("api_key missing")

    async def generate(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:  # pragma: no cover
        del req
        yield ChatEvent.message_done()


# ---------------------------------------------------------------------------
# Fixture:每个测试装一种 provider 进 registry,拿 entry,跑完清理
# ---------------------------------------------------------------------------


def _entry(type_name: str) -> ModelEntry:
    return ModelEntry(name=f"{type_name}-entry", type=type_name, options={})


@pytest.fixture
def _registry_isolated() -> Any:
    """fixture wrapper:测试结束后恢复 registry(只留 mock)。"""
    ProviderRegistry._reset()
    yield
    ProviderRegistry._reset()
    from chariot.providers.builtin.mock import MockProvider

    ProviderRegistry.register("mock", MockProvider)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProberSuccess:
    async def test_success_yields_ok_result(self, _registry_isolated: Any) -> None:
        ProviderRegistry.register("probe_ok", _SuccessProvider)
        result = await ProviderProber.probe(_entry("probe_ok"))
        assert result.ok is True
        assert result.error is None
        assert result.latency_ms >= 0


class TestProberYieldsErrorEvent:
    async def test_error_event_propagates_code(self, _registry_isolated: Any) -> None:
        ProviderRegistry.register("probe_yield_err", _YieldsErrorProvider)
        result = await ProviderProber.probe(_entry("probe_yield_err"))
        assert result.ok is False
        assert result.error is not None
        assert result.error.code == "upstream_stream_error"
        assert "simulated mid-stream" in result.error.message


class TestProberProviderErrorRaised:
    async def test_provider_error_propagates_code(self, _registry_isolated: Any) -> None:
        ProviderRegistry.register("probe_raises_pe", _RaisesProviderErrorProvider)
        result = await ProviderProber.probe(_entry("probe_raises_pe"))
        assert result.ok is False
        assert result.error is not None
        assert result.error.code == "upstream_auth_failed"
        assert "401" in result.error.message


class TestProberConfigError:
    async def test_build_failure_yields_config_error(self, _registry_isolated: Any) -> None:
        ProviderRegistry.register("probe_missing_key", _MissingApiKeyProvider)
        result = await ProviderProber.probe(_entry("probe_missing_key"))
        assert result.ok is False
        assert result.latency_ms == 0  # build 失败前 t0 还没流逝太多;契约要求 0
        assert result.error is not None
        assert result.error.code == "config_error"
        assert "api_key" in result.error.message

    async def test_unknown_type_yields_config_error(self, _registry_isolated: Any) -> None:
        """ProviderRegistry 未注册 type → ConfigError → code='config_error'。"""
        result = await ProviderProber.probe(_entry("never_registered"))
        assert result.ok is False
        assert result.latency_ms == 0
        assert result.error is not None
        assert result.error.code == "config_error"


class TestProberIncompleteStream:
    async def test_no_terminal_event_marks_incomplete(self, _registry_isolated: Any) -> None:
        ProviderRegistry.register("probe_incomplete", _IncompleteStreamProvider)
        result = await ProviderProber.probe(_entry("probe_incomplete"))
        assert result.ok is False
        assert result.error is not None
        assert result.error.code == "incomplete_stream"


class TestProberGenericExceptionFallback:
    async def test_unexpected_exception_caught_as_internal_error(
        self, _registry_isolated: Any
    ) -> None:
        ProviderRegistry.register("probe_generic_exc", _GenericExceptionProvider)
        result = await ProviderProber.probe(_entry("probe_generic_exc"))
        assert result.ok is False
        assert result.error is not None
        assert result.error.code == "probe_internal_error"
        assert "boom" in result.error.message

"""ModelProber 测试 —— 探针 4 条主路径。

- mock entry 直接成功(不发 HTTP)
- entry build 失败(unknown type / 缺 api_key)→ ok=False, code=config_error
- 上游 raise ServiceError(模拟 401 / 502 / 网络错)→ ok=False, error 透传
- 兜底:respond raise 非 ServiceError 异常 → code=probe_internal_error

注册表的隔离由 conftest 的 `isolate_model_registry` autouse fixture 负责,
本文件用例可以放心 register fake type。
"""

from __future__ import annotations

from typing import Any, Self

import pytest
from fastapi.responses import Response

from chariot.agent.config import ProviderEntry
from chariot.server.model.base import Model
from chariot.server.model.registry import ModelRegistry
from chariot.server.service.exceptions import ServiceError
from chariot.server.service.model_prober import ModelProber

# ---------- 测试用 model 实现 ----------


class _AlwaysOkModel(Model):
    name = "fake-ok"

    @classmethod
    def from_config(cls, options: dict[str, Any]) -> Self:
        del options
        return cls()

    async def respond(self, body: bytes, *, stream: bool) -> Response:
        del body, stream
        return Response(content=b'{"ok":1}', status_code=200, media_type="application/json")


class _ServiceErrorModel(Model):
    name = "fake-svc-err"

    @classmethod
    def from_config(cls, options: dict[str, Any]) -> Self:
        del options
        return cls()

    async def respond(self, body: bytes, *, stream: bool) -> Response:
        del body, stream
        raise ServiceError(
            status=502,
            code="upstream_auth_failed",
            message="模拟 401 上游",
        )


class _UnexpectedErrorModel(Model):
    name = "fake-boom"

    @classmethod
    def from_config(cls, options: dict[str, Any]) -> Self:
        del options
        return cls()

    async def respond(self, body: bytes, *, stream: bool) -> Response:
        del body, stream
        raise RuntimeError("意料外错误")


# ---------- 主路径 ----------


async def test_probe_mock_entry_succeeds() -> None:
    """mock 走本地 echo,不发 HTTP,probe 必通。"""
    entry = ProviderEntry(name="m", type="mock", options={})
    result = await ModelProber.probe(entry)
    assert result.ok is True
    assert result.error is None
    assert result.latency_ms >= 0


async def test_probe_unknown_type_returns_config_error() -> None:
    """type 未注册 → ModelRegistry.build 抛 ConfigError → probe 包成 config_error。"""
    entry = ProviderEntry(name="x", type="no_such_type", options={})
    result = await ModelProber.probe(entry)
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "config_error"
    assert "no_such_type" in result.error.message
    # build 阶段失败,latency 应为 0(还没发请求)
    assert result.latency_ms == 0


async def test_probe_anthropic_missing_api_key_returns_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """anthropic 没填 api_key 也没 export env → from_config 抛 ConfigError。"""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    entry = ProviderEntry(
        name="claude",
        type="anthropic",
        options={"model": "claude-opus-4-5"},
    )
    result = await ModelProber.probe(entry)
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "config_error"
    assert "api_key" in result.error.message


async def test_probe_upstream_service_error_propagates_code() -> None:
    """respond raise ServiceError → ok=False,code/message 直接透传。"""
    ModelRegistry.register("fake_svc_err", _ServiceErrorModel)
    entry = ProviderEntry(name="x", type="fake_svc_err", options={})
    result = await ModelProber.probe(entry)
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "upstream_auth_failed"
    assert "401" in result.error.message
    # respond 阶段失败,latency >= 0(发过请求)
    assert result.latency_ms >= 0


async def test_probe_unexpected_exception_caught_as_internal_error() -> None:
    """respond raise 非 ServiceError 异常 → 兜底成 probe_internal_error,不上抛。"""
    ModelRegistry.register("fake_boom", _UnexpectedErrorModel)
    entry = ProviderEntry(name="x", type="fake_boom", options={})
    result = await ModelProber.probe(entry)
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "probe_internal_error"
    assert "意料外错误" in result.error.message


async def test_probe_ok_model_returns_ok() -> None:
    """正常 respond 返 200 → ok=True。"""
    ModelRegistry.register("fake_ok", _AlwaysOkModel)
    entry = ProviderEntry(name="x", type="fake_ok", options={})
    result = await ModelProber.probe(entry)
    assert result.ok is True
    assert result.error is None

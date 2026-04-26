"""Agent 测试 —— 覆盖 handle() 流程 + log 落地 + 单例管理。"""

from __future__ import annotations

import json

import pytest
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.server.agent import Agent
from chariot.server.config import ChariotConfig, ModelEntry
from chariot.server.database.models import LogEntry
from chariot.server.model.mock import MockModel, mock_model
from chariot.server.service.exceptions import ServiceError


class _SpyModel:
    """记录调用参数,按固定响应或异常回复。"""

    name = "spy"

    def __init__(self, *, raise_exc: Exception | None = None) -> None:
        self.calls: list[tuple[bytes, bool]] = []
        self._raise = raise_exc

    async def respond(self, body: bytes, *, stream: bool) -> Response:
        self.calls.append((body, stream))
        if self._raise is not None:
            raise self._raise
        return Response(content=b'{"ok": true}', status_code=200, media_type="application/json")


# ---------- 构造 + 单例管理 ----------


def test_constructor_defaults_to_mock() -> None:
    """`Agent()` 不传 model 时 fallback 到 MockModel 单例。"""
    a = Agent()
    assert isinstance(a.model, MockModel)
    assert a.model is mock_model


def test_constructor_accepts_explicit_model() -> None:
    spy = _SpyModel()
    a = Agent(model=spy)
    assert a.model is spy


def test_current_without_install_raises() -> None:
    Agent.uninstall()
    with pytest.raises(RuntimeError, match="未安装"):
        Agent.current()


def test_install_and_current() -> None:
    Agent.uninstall()
    a = Agent.install()
    assert Agent.current() is a
    Agent.uninstall()


def test_install_replaces_previous() -> None:
    Agent.uninstall()
    a1 = Agent.install()
    a2 = Agent.install(model=_SpyModel())
    assert Agent.current() is a2
    assert a2 is not a1
    Agent.uninstall()


def test_install_with_explicit_model() -> None:
    Agent.uninstall()
    spy = _SpyModel()
    a = Agent.install(model=spy)
    assert a.model is spy
    assert Agent.current().model is spy
    Agent.uninstall()


# ---------- install_from_config ----------


def test_install_from_config_empty_uses_mock() -> None:
    """空配置 → MockModel fallback。"""
    Agent.uninstall()
    a = Agent.install_from_config(ChariotConfig.empty())
    assert isinstance(a.model, MockModel)
    Agent.uninstall()


def test_install_from_config_active_uses_registry() -> None:
    """有 active entry → 走 ModelRegistry.build,本例 type="mock" 仍然返 MockModel。"""
    Agent.uninstall()
    config = ChariotConfig(
        models=(ModelEntry(name="m", type="mock", options={}),),
        active="m",
    )
    a = Agent.install_from_config(config)
    assert isinstance(a.model, MockModel)
    assert Agent.current() is a
    Agent.uninstall()


def test_install_from_config_no_active_uses_mock() -> None:
    """有 models 但没设 active → 也走 fallback。"""
    Agent.uninstall()
    config = ChariotConfig(
        models=(ModelEntry(name="m", type="mock", options={}),),
        active=None,
    )
    a = Agent.install_from_config(config)
    assert isinstance(a.model, MockModel)
    Agent.uninstall()


# ---------- handle() 转发契约 ----------


async def test_handle_calls_model_with_body(session: AsyncSession) -> None:
    spy = _SpyModel()
    agent = Agent(model=spy)
    body = json.dumps({"model": "foo", "messages": []}).encode("utf-8")

    resp = await agent.handle(body)

    assert resp.status_code == 200
    assert len(spy.calls) == 1
    called_body, called_stream = spy.calls[0]
    assert called_body is body
    assert called_stream is False  # body 里没 stream: True


async def test_handle_detects_stream_flag(session: AsyncSession) -> None:
    """body 里 stream=true 时 Agent 透传 stream=True 给 Model。"""
    spy = _SpyModel()
    agent = Agent(model=spy)
    body = json.dumps({"model": "x", "stream": True, "messages": []}).encode("utf-8")

    await agent.handle(body)
    _, is_stream = spy.calls[0]
    assert is_stream is True


async def test_handle_invalid_json_still_forwards_with_stream_false(
    session: AsyncSession,
) -> None:
    """非法 JSON 不在 Agent 层 raise —— 让 Model 自己决定怎么处理。"""
    spy = _SpyModel()
    agent = Agent(model=spy)
    body = b"not json"

    resp = await agent.handle(body)
    assert resp.status_code == 200
    _, is_stream = spy.calls[0]
    assert is_stream is False  # 探测失败默认 False


# ---------- 日志落地 ----------


async def test_handle_writes_log_on_success(session: AsyncSession) -> None:
    """成功请求落一条 logs 记录,status=ok,抽出 body.model。"""
    agent = Agent(model=_SpyModel())
    body = json.dumps({"model": "claude-haiku-4-5", "messages": []}).encode("utf-8")

    await agent.handle(body)

    rows = (await session.execute(select(LogEntry))).scalars().all()
    assert len(rows) == 1
    log = rows[0]
    assert log.status == "ok"
    assert log.model == "claude-haiku-4-5"
    assert log.error is None
    assert log.latency_ms is not None and log.latency_ms >= 0


async def test_handle_writes_log_on_service_error(session: AsyncSession) -> None:
    """ServiceError 被 re-raise,但日志照记一条 status=error。"""
    err = ServiceError(status=400, code="bad_body", message="bad")
    agent = Agent(model=_SpyModel(raise_exc=err))
    body = json.dumps({"model": "m"}).encode("utf-8")

    with pytest.raises(ServiceError):
        await agent.handle(body)

    rows = (await session.execute(select(LogEntry))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "error"
    assert rows[0].error is not None
    assert "bad_body" in rows[0].error


async def test_handle_writes_log_on_generic_exception(session: AsyncSession) -> None:
    """非 ServiceError 也记 error 日志 + re-raise。"""
    agent = Agent(model=_SpyModel(raise_exc=RuntimeError("boom")))
    body = json.dumps({"model": "m"}).encode("utf-8")

    with pytest.raises(RuntimeError, match="boom"):
        await agent.handle(body)

    rows = (await session.execute(select(LogEntry))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "error"
    assert rows[0].error == "boom"

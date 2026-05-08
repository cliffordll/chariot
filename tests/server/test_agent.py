"""Agent 测试 —— handle() 路由 + 日志落地 + 单例管理。

0.3.1 路由模型重构后 Agent 不再持有单一 active model,而是 `name → Model` 字典。
请求路径:client 在 body.model 写 entry name → Agent 查字典 → 调 model.respond。

conftest 的 `clean_agent_state` autouse fixture 在每个用例后清 `Agent` 单例,
所以测试不需要显式 `Agent.uninstall()` 收尾。需要 spy model 的用例用
`make_test_agent` fixture 直接注入字典。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Self

import pytest
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.config import ChariotConfig, ProviderEntry
from chariot.database.models import LogEntry
from chariot.server.agent import Agent
from chariot.server.model.base import Model
from chariot.server.model.mock import MockModel
from chariot.server.service.exceptions import ServiceError

MakeTestAgent = Callable[[dict[str, Model]], Agent]


class _SpyModel(Model):
    """记录调用参数,按固定响应或异常回复。"""

    name = "spy"

    def __init__(self, *, raise_exc: Exception | None = None) -> None:
        self.calls: list[tuple[bytes, bool]] = []
        self._raise = raise_exc

    @classmethod
    def from_config(cls, options: dict[str, Any]) -> Self:
        del options
        return cls()  # spy 不走 ModelRegistry,这里仅满足 ABC 契约

    async def respond(self, body: bytes, *, stream: bool) -> Response:
        self.calls.append((body, stream))
        if self._raise is not None:
            raise self._raise
        return Response(content=b'{"ok": true}', status_code=200, media_type="application/json")


# ---------- 单例管理 ----------


def test_current_without_install_raises() -> None:
    Agent.uninstall()
    with pytest.raises(RuntimeError, match="未安装"):
        Agent.current()


def test_install_from_empty_config() -> None:
    """空配置 → models 字典空。"""
    a = Agent.install_from_config(ChariotConfig.empty())
    assert a.models == {}
    assert Agent.current() is a


def test_install_from_config_builds_dict() -> None:
    """有 entry → ModelRegistry.build,字典 keyed by name。"""
    config = ChariotConfig(
        providers=(
            ProviderEntry(name="m1", type="mock", options={}),
            ProviderEntry(name="m2", type="mock", options={}),
        ),
    )
    a = Agent.install_from_config(config)
    assert set(a.models.keys()) == {"m1", "m2"}
    assert isinstance(a.models["m1"], MockModel)


def test_install_from_config_is_idempotent_full_reset() -> None:
    """install_from_config 是幂等的全量重置:再次调用,删除的 entry 清出字典。"""
    Agent.install_from_config(
        ChariotConfig(
            providers=(
                ProviderEntry(name="a", type="mock", options={}),
                ProviderEntry(name="b", type="mock", options={}),
            )
        )
    )
    Agent.install_from_config(
        ChariotConfig(providers=(ProviderEntry(name="a", type="mock", options={}),))
    )
    a = Agent.current()
    assert set(a.models.keys()) == {"a"}


# ---------- handle() 路由契约 ----------


async def test_handle_routes_to_model_by_body_name(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
) -> None:
    spy_a = _SpyModel()
    spy_b = _SpyModel()
    agent = make_test_agent({"a": spy_a, "b": spy_b})
    body = json.dumps({"model": "b", "messages": []}).encode("utf-8")

    resp = await agent.handle(body)

    assert resp.status_code == 200
    assert len(spy_b.calls) == 1
    assert len(spy_a.calls) == 0
    called_body, called_stream = spy_b.calls[0]
    assert called_body is body
    assert called_stream is False


async def test_handle_unknown_model_name_raises_400(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
) -> None:
    agent = make_test_agent({"mock": _SpyModel()})
    body = json.dumps({"model": "ghost", "messages": []}).encode("utf-8")

    with pytest.raises(ServiceError) as exc:
        await agent.handle(body)
    assert exc.value.status == 400
    assert exc.value.code == "unknown_model_name"


async def test_handle_missing_body_model_raises_400(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
) -> None:
    """body 没 model 字段 → 400。"""
    agent = make_test_agent({"mock": _SpyModel()})
    body = json.dumps({"messages": []}).encode("utf-8")

    with pytest.raises(ServiceError) as exc:
        await agent.handle(body)
    assert exc.value.status == 400
    assert exc.value.code == "unknown_model_name"


async def test_handle_invalid_json_raises_400(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
) -> None:
    agent = make_test_agent({"mock": _SpyModel()})
    with pytest.raises(ServiceError) as exc:
        await agent.handle(b"not json")
    assert exc.value.status == 400


async def test_handle_detects_stream_flag(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
) -> None:
    """body 里 stream=true 时 Agent 透传 stream=True 给 Model。"""
    spy = _SpyModel()
    agent = make_test_agent({"m": spy})
    body = json.dumps({"model": "m", "stream": True, "messages": []}).encode("utf-8")

    await agent.handle(body)
    _, is_stream = spy.calls[0]
    assert is_stream is True


# ---------- 日志落地 ----------


async def test_handle_writes_log_on_success(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
) -> None:
    """成功请求落一条 logs 记录,status=ok,model=客户端写的 entry name。"""
    agent = make_test_agent({"claude-prod": _SpyModel()})
    body = json.dumps({"model": "claude-prod", "messages": []}).encode("utf-8")

    await agent.handle(body)

    rows = (await session.execute(select(LogEntry))).scalars().all()
    assert len(rows) == 1
    log = rows[0]
    assert log.status == "ok"
    assert log.provider == "claude-prod"
    assert log.error is None
    assert log.latency_ms is not None and log.latency_ms >= 0


async def test_handle_writes_log_on_unknown_model(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
) -> None:
    """unknown_model_name 也记 error 日志。"""
    agent = make_test_agent({"mock": _SpyModel()})
    body = json.dumps({"model": "ghost"}).encode("utf-8")

    with pytest.raises(ServiceError):
        await agent.handle(body)

    rows = (await session.execute(select(LogEntry))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "error"
    assert rows[0].error is not None
    assert "unknown_model_name" in rows[0].error


async def test_handle_writes_log_on_model_service_error(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
) -> None:
    """Model 抛 ServiceError → re-raise + 落 error 日志。"""
    err = ServiceError(status=502, code="upstream_unreachable", message="boom")
    agent = make_test_agent({"m": _SpyModel(raise_exc=err)})
    body = json.dumps({"model": "m"}).encode("utf-8")

    with pytest.raises(ServiceError):
        await agent.handle(body)

    rows = (await session.execute(select(LogEntry))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "error"
    assert rows[0].error is not None
    assert "upstream_unreachable" in rows[0].error


async def test_handle_writes_log_on_generic_exception(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
) -> None:
    agent = make_test_agent({"m": _SpyModel(raise_exc=RuntimeError("boom"))})
    body = json.dumps({"model": "m"}).encode("utf-8")

    with pytest.raises(RuntimeError, match="boom"):
        await agent.handle(body)

    rows = (await session.execute(select(LogEntry))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "error"
    assert rows[0].error == "boom"

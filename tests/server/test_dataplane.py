"""Dataplane endpoint 测试 —— 单端点 `/v1/messages` 转发到 Agent。

0.3.1 路由模型重构后:
- client 必须在 body.model 写 entry name(chariot 的用户面 ID)
- Agent 按 name 路由到对应 Model 实例
- 缺失 / 未知 → 400 unknown_model_name
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any, Self

import pytest_asyncio
from fastapi import FastAPI
from fastapi.responses import Response
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.server.agent import Agent
from chariot.server.config import ChariotConfig, ModelEntry
from chariot.server.controller import dataplane_router, register_exception_handlers
from chariot.server.database.session import get_session
from chariot.server.model.base import Model

MakeTestAgent = Callable[[dict[str, Model]], Agent]


class _CapturingModel(Model):
    """记录最近一次 respond 调用的参数。"""

    name = "capturing"

    def __init__(self) -> None:
        self.last: tuple[bytes, bool] | None = None

    @classmethod
    def from_config(cls, options: dict[str, Any]) -> Self:
        del options
        return cls()  # 测试 spy,不走 ModelRegistry,仅为满足 ABC 契约

    async def respond(self, body: bytes, *, stream: bool) -> Response:
        self.last = (body, stream)
        return Response(
            content=json.dumps({"ok": True}).encode("utf-8"),
            status_code=200,
            media_type="application/json",
        )


@pytest_asyncio.fixture
async def client_and_model(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
) -> AsyncIterator[tuple[AsyncClient, _CapturingModel]]:
    model = _CapturingModel()
    # 注入 spy model 到 name="spy" 路由项;Agent 单例 cleanup 由 conftest 的
    # clean_agent_state autouse fixture 接管
    make_test_agent({"spy": model})

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(dataplane_router)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _override_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c, model


# ---------- 唯一端点 → Agent.handle ----------


async def test_messages_endpoint_routes_by_body_model(
    client_and_model: tuple[AsyncClient, _CapturingModel],
) -> None:
    client, model = client_and_model
    body = {"model": "spy", "messages": [{"role": "user", "content": "hi"}]}
    resp = await client.post("/v1/messages", json=body)

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert model.last is not None


async def test_messages_unknown_model_returns_400(
    client_and_model: tuple[AsyncClient, _CapturingModel],
) -> None:
    client, _ = client_and_model
    resp = await client.post(
        "/v1/messages",
        json={"model": "ghost", "messages": []},
    )
    assert resp.status_code == 400
    assert "unknown_model_name" in resp.text


async def test_messages_missing_model_field_returns_400(
    client_and_model: tuple[AsyncClient, _CapturingModel],
) -> None:
    client, _ = client_and_model
    resp = await client.post("/v1/messages", json={"messages": []})
    assert resp.status_code == 400
    assert "unknown_model_name" in resp.text


# ---------- OpenAI 端点已下线 —— 应该返 404 ----------


async def test_chat_completions_endpoint_removed(
    client_and_model: tuple[AsyncClient, _CapturingModel],
) -> None:
    client, _ = client_and_model
    resp = await client.post("/v1/chat/completions", json={"model": "x", "messages": []})
    assert resp.status_code == 404


async def test_responses_endpoint_removed(
    client_and_model: tuple[AsyncClient, _CapturingModel],
) -> None:
    client, _ = client_and_model
    resp = await client.post("/v1/responses", json={"model": "x", "input": "hi"})
    assert resp.status_code == 404


# ---------- body 透传 + stream 标志探测 ----------


async def test_body_is_forwarded_verbatim(
    client_and_model: tuple[AsyncClient, _CapturingModel],
) -> None:
    client, model = client_and_model
    body = {"model": "spy", "messages": [{"role": "user", "content": "unique-marker-42"}]}
    await client.post("/v1/messages", json=body)

    assert model.last is not None
    received_body, _ = model.last
    assert b"unique-marker-42" in received_body


async def test_stream_flag_propagated(
    client_and_model: tuple[AsyncClient, _CapturingModel],
) -> None:
    client, model = client_and_model
    await client.post(
        "/v1/messages",
        json={"model": "spy", "stream": True, "messages": []},
    )
    assert model.last is not None
    assert model.last[1] is True


async def test_non_stream_when_flag_missing(
    client_and_model: tuple[AsyncClient, _CapturingModel],
) -> None:
    client, model = client_and_model
    await client.post("/v1/messages", json={"model": "spy", "messages": []})
    assert model.last is not None
    assert model.last[1] is False


# ---------- 用 MockModel entry 的端到端 ----------


async def test_mock_entry_end_to_end(session: AsyncSession) -> None:
    """seed 一条 mock entry,验证 echo 文本一路打到 HTTP 响应。"""
    Agent.install_from_config(
        ChariotConfig(models=(ModelEntry(name="mock", type="mock", options={}),))
    )

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(dataplane_router)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _override_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/v1/messages",
            json={
                "model": "mock",  # 写 entry name
                "max_tokens": 32,
                "messages": [{"role": "user", "content": "marco"}],
            },
        )
    assert r.status_code == 200
    data = r.json()
    assert data["content"][0]["text"].startswith("[mock echo]")
    assert data["content"][0]["text"].endswith("marco")

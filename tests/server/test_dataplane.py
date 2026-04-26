"""Dataplane endpoint 测试 —— 单端点 `/v1/messages` 转发到 Agent。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest_asyncio
from fastapi import FastAPI
from fastapi.responses import Response
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.server.agent import Agent
from chariot.server.controller import dataplane_router
from chariot.server.database.session import get_session
from chariot.shared.protocols import Protocol


class _CapturingModel:
    """记录最近一次 respond 调用的参数。"""

    name = "capturing"

    def __init__(self) -> None:
        self.last: tuple[Protocol, bytes, bool] | None = None

    async def respond(self, protocol: Protocol, body: bytes, *, stream: bool) -> Response:
        self.last = (protocol, body, stream)
        return Response(
            content=json.dumps({"ok": True, "p": protocol.value}).encode("utf-8"),
            status_code=200,
            media_type="application/json",
        )


@pytest_asyncio.fixture
async def client_and_model(
    session: AsyncSession,
) -> AsyncIterator[tuple[AsyncClient, _CapturingModel]]:
    model = _CapturingModel()
    Agent.uninstall()
    # 注入 spy model,绕过默认 MockModel
    Agent.install(model=model)

    app = FastAPI()
    app.include_router(dataplane_router)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _override_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c, model

    Agent.uninstall()


# ---------- 唯一端点 → Messages 协议 ----------


async def test_messages_endpoint_forwards_messages_protocol(
    client_and_model: tuple[AsyncClient, _CapturingModel],
) -> None:
    client, model = client_and_model
    body = {"model": "claude-haiku-4-5", "messages": [{"role": "user", "content": "hi"}]}
    resp = await client.post("/v1/messages", json=body)

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "p": "messages"}
    assert model.last is not None
    assert model.last[0] is Protocol.MESSAGES


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
    body = {"model": "x", "messages": [{"role": "user", "content": "unique-marker-42"}]}
    await client.post("/v1/messages", json=body)

    assert model.last is not None
    _, received_body, _ = model.last
    assert b"unique-marker-42" in received_body


async def test_stream_flag_propagated(
    client_and_model: tuple[AsyncClient, _CapturingModel],
) -> None:
    client, model = client_and_model
    await client.post(
        "/v1/messages",
        json={"model": "x", "stream": True, "messages": []},
    )
    assert model.last is not None
    assert model.last[2] is True


async def test_non_stream_when_flag_missing(
    client_and_model: tuple[AsyncClient, _CapturingModel],
) -> None:
    client, model = client_and_model
    await client.post("/v1/messages", json={"model": "x", "messages": []})
    assert model.last is not None
    assert model.last[2] is False


# ---------- 用默认 MockModel 的端到端 ----------


async def test_default_mock_agent_end_to_end(session: AsyncSession) -> None:
    """不注入 spy,走默认 MockModel,验证 echo 文本一路打到 HTTP 响应。"""
    Agent.uninstall()
    Agent.install()  # MockModel

    app = FastAPI()
    app.include_router(dataplane_router)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _override_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/v1/messages",
            json={
                "model": "x",
                "max_tokens": 32,
                "messages": [{"role": "user", "content": "marco"}],
            },
        )
    assert r.status_code == 200
    data = r.json()
    assert data["content"][0]["text"].startswith("[mock echo]")
    assert data["content"][0]["text"].endswith("marco")

    Agent.uninstall()

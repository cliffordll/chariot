"""/admin/conversations 端点测试 —— 0.4.0 多轮会话管理。

覆盖:
- GET /admin/conversations(空 / 有 / 分页)
- GET /admin/conversations/{id}(详情含 messages / 404 / 非法 ULID 400)
- POST /admin/conversations(server 生成 ULID / client 给 ULID / 重复 409 / 非法 400)
- DELETE /admin/conversations/{id}(204 / cascade messages / 404)
- PATCH /admin/conversations/{id}(改 title / 404)
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.server.agent import Agent
from chariot.server.config import ChariotConfig
from chariot.server.controller import admin_router, register_exception_handlers
from chariot.server.database.session import get_session
from chariot.server.repository.conversation_repo import ConversationRepo

ULID_A = "01JD7K8YQXM2N8R5VF3PCWE4ZB"
ULID_B = "01JD7K8YQXM2N8R5VF3PCWE4ZC"


@pytest_asyncio.fixture
async def admin_client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    Agent.install_from_config(ChariotConfig.empty())
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(admin_router, prefix="/admin")

    async def _override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


# ---------- GET list ----------


async def test_list_conversations_empty(admin_client: AsyncClient) -> None:
    r = await admin_client.get("/admin/conversations")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["limit"] == 50
    assert body["offset"] == 0


async def test_list_conversations_with_entries(
    admin_client: AsyncClient, session: AsyncSession
) -> None:
    repo = ConversationRepo(session)
    await repo.create(ULID_A, title="第一次")
    await repo.create(ULID_B, title="第二次")
    await repo.append_message(ULID_A, "user", "x")

    r = await admin_client.get("/admin/conversations")
    assert r.status_code == 200
    items = r.json()["items"]
    assert {it["id"] for it in items} == {ULID_A, ULID_B}
    by_id = {it["id"]: it for it in items}
    assert by_id[ULID_A]["title"] == "第一次"
    assert by_id[ULID_A]["message_count"] == 1
    assert by_id[ULID_B]["message_count"] == 0


async def test_list_conversations_pagination(
    admin_client: AsyncClient, session: AsyncSession
) -> None:
    repo = ConversationRepo(session)
    for i in range(5):
        await repo.create(f"01JD000000000000000000000{i}")
    r = await admin_client.get("/admin/conversations?limit=2&offset=1")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 2
    assert body["limit"] == 2
    assert body["offset"] == 1


# ---------- GET detail ----------


async def test_get_conversation_detail(admin_client: AsyncClient, session: AsyncSession) -> None:
    repo = ConversationRepo(session)
    await repo.create(ULID_A, title="t")
    await repo.append_message(ULID_A, "user", "hi")
    await repo.append_message(ULID_A, "assistant", "hello", model_name="mock")

    r = await admin_client.get(f"/admin/conversations/{ULID_A}")
    assert r.status_code == 200
    body = r.json()
    assert body["conversation"]["id"] == ULID_A
    assert body["conversation"]["title"] == "t"
    assert len(body["messages"]) == 2
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][0]["seq"] == 0
    assert body["messages"][0]["content"] == "hi"
    assert body["messages"][1]["role"] == "assistant"
    assert body["messages"][1]["model_name"] == "mock"


async def test_get_conversation_not_found(admin_client: AsyncClient) -> None:
    r = await admin_client.get(f"/admin/conversations/{ULID_A}")
    assert r.status_code == 404
    assert "conversation_not_found" in r.text


async def test_get_conversation_invalid_ulid(admin_client: AsyncClient) -> None:
    r = await admin_client.get("/admin/conversations/short")
    assert r.status_code == 400
    assert "invalid_conversation_id" in r.text


# ---------- POST create ----------


async def test_create_conversation_server_generates_id(
    admin_client: AsyncClient,
) -> None:
    r = await admin_client.post("/admin/conversations", json={"title": "new"})
    assert r.status_code == 201
    body = r.json()
    assert body["title"] == "new"
    # server 生成 ULID:26 字符 base32 大写
    assert len(body["id"]) == 26
    assert body["id"].isupper() or any(c.isdigit() for c in body["id"])
    assert body["message_count"] == 0


async def test_create_conversation_client_provides_id(admin_client: AsyncClient) -> None:
    r = await admin_client.post(
        "/admin/conversations",
        json={"id": ULID_A, "title": "from-client"},
    )
    assert r.status_code == 201
    assert r.json()["id"] == ULID_A


async def test_create_conversation_duplicate_id_returns_409(
    admin_client: AsyncClient, session: AsyncSession
) -> None:
    await ConversationRepo(session).create(ULID_A)
    r = await admin_client.post("/admin/conversations", json={"id": ULID_A})
    assert r.status_code == 409
    assert "conversation_id_exists" in r.text


async def test_create_conversation_invalid_ulid_returns_400(
    admin_client: AsyncClient,
) -> None:
    r = await admin_client.post("/admin/conversations", json={"id": "not-a-ulid"})
    assert r.status_code == 400
    assert "invalid_conversation_id" in r.text


async def test_create_conversation_no_title(admin_client: AsyncClient) -> None:
    r = await admin_client.post("/admin/conversations", json={})
    assert r.status_code == 201
    assert r.json()["title"] is None


# ---------- DELETE ----------


async def test_delete_conversation(admin_client: AsyncClient, session: AsyncSession) -> None:
    repo = ConversationRepo(session)
    await repo.create(ULID_A)
    await repo.append_message(ULID_A, "user", "x")
    r = await admin_client.delete(f"/admin/conversations/{ULID_A}")
    assert r.status_code == 204
    assert await repo.get(ULID_A) is None
    # cascade:messages 也清了
    assert await repo.load_messages_as_anthropic(ULID_A) == []


async def test_delete_conversation_not_found(admin_client: AsyncClient) -> None:
    r = await admin_client.delete(f"/admin/conversations/{ULID_A}")
    assert r.status_code == 404


async def test_delete_conversation_invalid_ulid(admin_client: AsyncClient) -> None:
    r = await admin_client.delete("/admin/conversations/short")
    assert r.status_code == 400


# ---------- PATCH title ----------


async def test_patch_title(admin_client: AsyncClient, session: AsyncSession) -> None:
    await ConversationRepo(session).create(ULID_A, title="旧")
    r = await admin_client.patch(
        f"/admin/conversations/{ULID_A}",
        json={"title": "新"},
    )
    assert r.status_code == 200
    assert r.json()["title"] == "新"


async def test_patch_title_to_none(admin_client: AsyncClient, session: AsyncSession) -> None:
    await ConversationRepo(session).create(ULID_A, title="旧")
    r = await admin_client.patch(
        f"/admin/conversations/{ULID_A}",
        json={"title": None},
    )
    assert r.status_code == 200
    assert r.json()["title"] is None


async def test_patch_title_not_found(admin_client: AsyncClient) -> None:
    r = await admin_client.patch(
        f"/admin/conversations/{ULID_A}",
        json={"title": "x"},
    )
    assert r.status_code == 404

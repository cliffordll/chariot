"""/admin/models 端点测试 —— GET 列表 / 探针 / entries CRUD。

0.3.1 路由模型重构后(active 概念删除):
- POST /admin/models 切 active 端点已删除
- GET /admin/models 不返 active 字段;entries 加 params
- entries CRUD 加 params 字段;delete 任意 entry 都允许(无 cannot_delete_active)

本文件覆盖:
- GET /admin/models 列表(空 / 有 entries / params 字段)
- POST /admin/models/{name}/probe 探针
- POST /admin/models/entries 创建(params / 409 / 400)
- PUT /admin/models/entries/{name} 更新(改 options / params 都立刻 rebuild)
- DELETE /admin/models/entries/{name} 删除
- POST /admin/models/entries/{name}/duplicate 复制(含 params)
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.config import ChariotConfig
from chariot.database.session import get_session
from chariot.repos.model_repo import ModelRepo
from chariot.server.agent import Agent
from chariot.server.controller import admin_router, register_exception_handlers


@pytest_asyncio.fixture
async def admin_client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """带 admin router + 异常处理器的测试客户端。"""
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(admin_router, prefix="/admin")

    async def _override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c

    Agent.uninstall()


async def _seed_and_install(
    session: AsyncSession,
    entries: list[tuple[str, str, dict, dict]],
) -> None:
    """把 (name, type, options, params) 写入 DB,然后 install Agent。"""
    repo = ModelRepo(session)
    for name, type_, options, params in entries:
        await repo.create(name=name, type=type_, options=options, params=params)
    config = await ChariotConfig.from_db(session)
    Agent.install_from_config(config)


# ---------- /admin/models GET ----------


async def test_get_models_with_entries(admin_client: AsyncClient, session: AsyncSession) -> None:
    await _seed_and_install(
        session,
        [("m1", "mock", {}, {}), ("m2", "mock", {}, {"temperature": 0.5})],
    )

    r = await admin_client.get("/admin/models")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] == ["m1", "m2"]
    assert "active" not in body  # 0.3.1 删除
    assert "mock" in body["types"]
    assert "anthropic" in body["types"]
    # entries 加 params
    e2 = next(e for e in body["entries"] if e["name"] == "m2")
    assert e2["params"] == {"temperature": 0.5}


async def test_get_models_empty_db(admin_client: AsyncClient) -> None:
    """空 DB → available 空,types 仍列出已注册的。"""
    Agent.install_from_config(ChariotConfig.empty())

    r = await admin_client.get("/admin/models")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] == []
    assert body["entries"] == []
    assert "mock" in body["types"]


# ---------- /admin/models/{name}/probe POST ----------


async def test_probe_mock_entry_returns_ok(
    admin_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_and_install(session, [("m1", "mock", {}, {})])

    r = await admin_client.post("/admin/models/m1/probe")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["error"] is None
    assert body["latency_ms"] >= 0


async def test_probe_unknown_name_returns_404(
    admin_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_and_install(session, [("m1", "mock", {}, {})])

    r = await admin_client.post("/admin/models/ghost/probe")
    assert r.status_code == 404
    assert "ghost" in r.text


# ---------- /admin/models/entries POST(create)----------


async def test_create_entry_persists_and_refreshes_config(
    admin_client: AsyncClient, session: AsyncSession
) -> None:
    Agent.install_from_config(ChariotConfig.empty())

    r = await admin_client.post(
        "/admin/models/entries",
        json={"name": "new-claude", "type": "mock", "options": {"k": "v"}},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "new-claude"
    assert body["options"] == {"k": "v"}
    assert body["params"] == {}  # 默认空

    # DB 持久化
    entry = await ModelRepo(session).get_entry("new-claude")
    assert entry is not None and entry.options == {"k": "v"}
    # Agent.models 字典同步
    assert "new-claude" in Agent.current().models


async def test_create_entry_with_params(admin_client: AsyncClient) -> None:
    Agent.install_from_config(ChariotConfig.empty())

    r = await admin_client.post(
        "/admin/models/entries",
        json={
            "name": "a",
            "type": "mock",
            "options": {},
            "params": {"temperature": 0.7, "max_tokens": 2048},
        },
    )
    assert r.status_code == 201
    assert r.json()["params"] == {"temperature": 0.7, "max_tokens": 2048}


async def test_create_entry_duplicate_name_returns_409(
    admin_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_and_install(session, [("x", "mock", {}, {})])

    r = await admin_client.post(
        "/admin/models/entries",
        json={"name": "x", "type": "mock", "options": {}},
    )
    assert r.status_code == 409
    assert "已存在" in r.text


async def test_create_entry_unknown_type_returns_400(admin_client: AsyncClient) -> None:
    Agent.install_from_config(ChariotConfig.empty())

    r = await admin_client.post(
        "/admin/models/entries",
        json={"name": "x", "type": "no_such_type", "options": {}},
    )
    assert r.status_code == 400
    assert "no_such_type" in r.text


# ---------- /admin/models/entries/{name} PUT(update)----------


async def test_update_entry_options(admin_client: AsyncClient, session: AsyncSession) -> None:
    await _seed_and_install(session, [("x", "mock", {"a": 1}, {})])

    r = await admin_client.put(
        "/admin/models/entries/x",
        json={"options": {"a": 999}},
    )
    assert r.status_code == 200
    assert r.json()["options"] == {"a": 999}

    entry = await ModelRepo(session).get_entry("x")
    assert entry is not None and entry.options == {"a": 999}


async def test_update_entry_params(admin_client: AsyncClient, session: AsyncSession) -> None:
    """改 params 不动 options。"""
    await _seed_and_install(session, [("x", "mock", {"a": 1}, {})])

    r = await admin_client.put(
        "/admin/models/entries/x",
        json={"params": {"temperature": 0.3}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["options"] == {"a": 1}
    assert body["params"] == {"temperature": 0.3}


async def test_update_entry_rebuilds_model_in_agent(
    admin_client: AsyncClient, session: AsyncSession
) -> None:
    """改 entry options → Agent 字典重建该 entry 实例。"""
    await _seed_and_install(session, [("a", "mock", {}, {})])
    old_instance = Agent.current().models["a"]

    r = await admin_client.put(
        "/admin/models/entries/a",
        json={"options": {"new_field": "new_value"}},
    )
    assert r.status_code == 200
    # rebuild 后是新实例
    new_instance = Agent.current().models["a"]
    assert new_instance is not old_instance


async def test_update_entry_unknown_name_returns_404(admin_client: AsyncClient) -> None:
    Agent.install_from_config(ChariotConfig.empty())

    r = await admin_client.put(
        "/admin/models/entries/ghost",
        json={"options": {}},
    )
    assert r.status_code == 404


async def test_update_entry_unknown_type_returns_400(
    admin_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_and_install(session, [("x", "mock", {}, {})])

    r = await admin_client.put(
        "/admin/models/entries/x",
        json={"type": "no_such_type"},
    )
    assert r.status_code == 400


# ---------- /admin/models/entries/{name} DELETE ----------


async def test_delete_entry_removes_row(admin_client: AsyncClient, session: AsyncSession) -> None:
    """0.3.1 起任意 entry 都能删(active 概念删除,无 cannot_delete_active)。"""
    await _seed_and_install(session, [("a", "mock", {}, {}), ("b", "mock", {}, {})])

    r = await admin_client.delete("/admin/models/entries/b")
    assert r.status_code == 204
    assert await ModelRepo(session).get_entry("b") is None
    # Agent.models 字典同步
    assert "b" not in Agent.current().models
    assert "a" in Agent.current().models


async def test_delete_only_entry_succeeds(admin_client: AsyncClient, session: AsyncSession) -> None:
    """0.3.1:删最后一个 entry 也允许;Agent.models 变空字典。"""
    await _seed_and_install(session, [("a", "mock", {}, {})])

    r = await admin_client.delete("/admin/models/entries/a")
    assert r.status_code == 204
    assert Agent.current().models == {}


async def test_delete_unknown_entry_returns_404(admin_client: AsyncClient) -> None:
    Agent.install_from_config(ChariotConfig.empty())

    r = await admin_client.delete("/admin/models/entries/ghost")
    assert r.status_code == 404


# ---------- /admin/models/entries/{name}/duplicate POST ----------


async def test_duplicate_entry_default_name(
    admin_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_and_install(
        session,
        [("src", "mock", {"k": "v"}, {"temperature": 0.5})],
    )

    r = await admin_client.post("/admin/models/entries/src/duplicate", json={})
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "src_copy"
    assert body["options"] == {"k": "v"}
    assert body["params"] == {"temperature": 0.5}  # params 也复制

    # DB 也有
    entry = await ModelRepo(session).get_entry("src_copy")
    assert entry is not None


async def test_duplicate_entry_explicit_as_name(
    admin_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_and_install(session, [("src", "mock", {}, {})])

    r = await admin_client.post(
        "/admin/models/entries/src/duplicate",
        json={"as": "my-copy"},
    )
    assert r.status_code == 201
    assert r.json()["name"] == "my-copy"


async def test_duplicate_entry_explicit_name_collision_returns_409(
    admin_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_and_install(
        session,
        [("src", "mock", {}, {}), ("taken", "mock", {}, {})],
    )

    r = await admin_client.post(
        "/admin/models/entries/src/duplicate",
        json={"as": "taken"},
    )
    assert r.status_code == 409


async def test_duplicate_unknown_src_returns_404(admin_client: AsyncClient) -> None:
    Agent.install_from_config(ChariotConfig.empty())

    r = await admin_client.post("/admin/models/entries/ghost/duplicate", json={})
    assert r.status_code == 404

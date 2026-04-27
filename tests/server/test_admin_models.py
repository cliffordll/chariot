"""/admin/models 端点测试 —— GET 列表 / 切 active / 探针 / entries CRUD。

0.3.0 起 entries 住 DB(`models` / `settings` 表),controller 写操作走
ModelRepo + 同步 Agent。本文件覆盖:

- GET /admin/models 列表(空 / 有 entries / active 标识)
- POST /admin/models 切 active(持久化 + rebuild)
- POST /admin/models/{name}/probe 探针
- POST /admin/models/entries 创建(409 / 400 错误路径)
- PUT /admin/models/entries/{name} 更新(改 active 自动 rebuild)
- DELETE /admin/models/entries/{name} 删除(active 拒绝)
- POST /admin/models/entries/{name}/duplicate 复制(default / explicit / 碰撞)
- Agent.switch_to 单元(找不到 → ModelNotFound)
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.server.agent import Agent
from chariot.server.config import ChariotConfig, ModelNotFound
from chariot.server.controller import admin_router, register_exception_handlers
from chariot.server.database.session import get_session
from chariot.server.repository.model_repo import ModelRepo


@pytest_asyncio.fixture
async def admin_client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """带 admin router + 异常处理器的测试客户端。

    每用例自管 entry seed:用 `_seed_db_and_install` helper 写 DB 并 install Agent。
    """
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
    entries: list[tuple[str, str, dict]],
    active: str | None,
) -> None:
    """把 (name, type, options) 三元组写入 DB,然后 ChariotConfig.from_db + install。"""
    repo = ModelRepo(session)
    for name, type_, options in entries:
        await repo.create(name=name, type=type_, options=options)
    if active is not None:
        await repo.set_active(active)
    config = await ChariotConfig.from_db(session)
    Agent.install_from_config(config)


# ---------- /admin/models GET ----------


async def test_get_models_with_entries(admin_client: AsyncClient, session: AsyncSession) -> None:
    await _seed_and_install(
        session,
        [("m1", "mock", {}), ("m2", "mock", {})],
        active="m1",
    )

    r = await admin_client.get("/admin/models")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] == ["m1", "m2"]
    assert body["active"] == "m1"
    assert "mock" in body["types"]
    assert "anthropic" in body["types"]


async def test_get_models_empty_db(admin_client: AsyncClient) -> None:
    """空 DB → available 空,active null,types 仍列出已注册的(import 副作用)。"""
    Agent.install_from_config(ChariotConfig.empty())

    r = await admin_client.get("/admin/models")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] == []
    assert body["active"] is None
    assert "mock" in body["types"]


# ---------- /admin/models POST(切 active)----------


async def test_post_models_switches_active(
    admin_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_and_install(
        session,
        [("m1", "mock", {}), ("m2", "mock", {})],
        active="m1",
    )
    assert Agent.active_name() == "m1"

    r = await admin_client.post("/admin/models", json={"name": "m2"})
    assert r.status_code == 200
    body = r.json()
    assert body["active"] == "m2"
    assert body["model"] == "mock-echo-v1"
    assert Agent.active_name() == "m2"

    # 持久化到 DB(GET 验证)
    persisted = await ModelRepo(session).get_active()
    assert persisted == "m2"


async def test_post_models_unknown_name_returns_400(
    admin_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_and_install(session, [("m1", "mock", {})], active="m1")

    r = await admin_client.post("/admin/models", json={"name": "ghost"})
    assert r.status_code == 400
    assert "ghost" in r.text


# ---------- /admin/models/{name}/probe POST ----------


async def test_probe_mock_entry_returns_ok(
    admin_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_and_install(session, [("m1", "mock", {})], active="m1")

    r = await admin_client.post("/admin/models/m1/probe")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["error"] is None
    assert body["latency_ms"] >= 0


async def test_probe_unknown_name_returns_404(
    admin_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_and_install(session, [("m1", "mock", {})], active="m1")

    r = await admin_client.post("/admin/models/ghost/probe")
    assert r.status_code == 404
    assert "ghost" in r.text


async def test_probe_does_not_affect_active(
    admin_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_and_install(session, [("m1", "mock", {}), ("m2", "mock", {})], active="m1")

    r = await admin_client.post("/admin/models/m2/probe")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert Agent.active_name() == "m1"


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

    # DB 持久化
    entry = await ModelRepo(session).get_entry("new-claude")
    assert entry is not None and entry.options == {"k": "v"}
    # Agent._config 缓存同步
    assert "new-claude" in [e.name for e in Agent.config().models]


async def test_create_entry_duplicate_name_returns_409(
    admin_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_and_install(session, [("x", "mock", {})], active="x")

    r = await admin_client.post(
        "/admin/models/entries",
        json={"name": "x", "type": "mock", "options": {}},
    )
    assert r.status_code == 409
    assert "已存在" in r.text


async def test_create_entry_unknown_type_returns_400(
    admin_client: AsyncClient,
) -> None:
    Agent.install_from_config(ChariotConfig.empty())

    r = await admin_client.post(
        "/admin/models/entries",
        json={"name": "x", "type": "no_such_type", "options": {}},
    )
    assert r.status_code == 400
    assert "no_such_type" in r.text


# ---------- /admin/models/entries/{name} PUT(update)----------


async def test_update_entry_options(admin_client: AsyncClient, session: AsyncSession) -> None:
    await _seed_and_install(session, [("x", "mock", {"a": 1})], active=None)

    r = await admin_client.put(
        "/admin/models/entries/x",
        json={"options": {"a": 999}},
    )
    assert r.status_code == 200
    assert r.json()["options"] == {"a": 999}

    entry = await ModelRepo(session).get_entry("x")
    assert entry is not None and entry.options == {"a": 999}


async def test_update_active_entry_rebuilds_model(
    admin_client: AsyncClient, session: AsyncSession
) -> None:
    """改 active entry 时 active model 实例要 rebuild(新 options 即时生效)。"""
    await _seed_and_install(session, [("a", "mock", {})], active="a")
    assert Agent.active_name() == "a"

    r = await admin_client.put(
        "/admin/models/entries/a",
        json={"options": {"new_field": "new_value"}},
    )
    assert r.status_code == 200
    # active 仍是 a(rebuild 不改 active 名,只换实例)
    assert Agent.active_name() == "a"


async def test_update_entry_unknown_name_returns_404(
    admin_client: AsyncClient,
) -> None:
    Agent.install_from_config(ChariotConfig.empty())

    r = await admin_client.put(
        "/admin/models/entries/ghost",
        json={"options": {}},
    )
    assert r.status_code == 404


async def test_update_entry_unknown_type_returns_400(
    admin_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_and_install(session, [("x", "mock", {})], active=None)

    r = await admin_client.put(
        "/admin/models/entries/x",
        json={"type": "no_such_type"},
    )
    assert r.status_code == 400


# ---------- /admin/models/entries/{name} DELETE ----------


async def test_delete_entry_removes_row(admin_client: AsyncClient, session: AsyncSession) -> None:
    await _seed_and_install(session, [("a", "mock", {}), ("b", "mock", {})], active="a")

    r = await admin_client.delete("/admin/models/entries/b")
    assert r.status_code == 204
    assert await ModelRepo(session).get_entry("b") is None
    # Agent._config 同步
    assert "b" not in [e.name for e in Agent.config().models]


async def test_delete_active_entry_returns_400(
    admin_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_and_install(session, [("a", "mock", {})], active="a")

    r = await admin_client.delete("/admin/models/entries/a")
    assert r.status_code == 400
    assert "cannot_delete_active" in r.text


async def test_delete_unknown_entry_returns_404(admin_client: AsyncClient) -> None:
    Agent.install_from_config(ChariotConfig.empty())

    r = await admin_client.delete("/admin/models/entries/ghost")
    assert r.status_code == 404


# ---------- /admin/models/entries/{name}/duplicate POST ----------


async def test_duplicate_entry_default_name(
    admin_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_and_install(session, [("src", "mock", {"k": "v"})], active=None)

    r = await admin_client.post("/admin/models/entries/src/duplicate", json={})
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "src_copy"
    assert body["options"] == {"k": "v"}

    # DB 也有
    entry = await ModelRepo(session).get_entry("src_copy")
    assert entry is not None


async def test_duplicate_entry_explicit_as_name(
    admin_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_and_install(session, [("src", "mock", {})], active=None)

    r = await admin_client.post("/admin/models/entries/src/duplicate", json={"as": "my-copy"})
    assert r.status_code == 201
    assert r.json()["name"] == "my-copy"


async def test_duplicate_entry_explicit_name_collision_returns_409(
    admin_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_and_install(session, [("src", "mock", {}), ("taken", "mock", {})], active=None)

    r = await admin_client.post("/admin/models/entries/src/duplicate", json={"as": "taken"})
    assert r.status_code == 409


async def test_duplicate_unknown_src_returns_404(admin_client: AsyncClient) -> None:
    Agent.install_from_config(ChariotConfig.empty())

    r = await admin_client.post("/admin/models/entries/ghost/duplicate", json={})
    assert r.status_code == 404


# ---------- Agent.switch_to 单元测试(纯内存路径)----------


def test_switch_to_unknown_name_raises_model_not_found() -> None:
    Agent.uninstall()
    from chariot.server.config import ModelEntry

    config = ChariotConfig(
        models=(ModelEntry(name="a", type="mock", options={}),),
        active="a",
    )
    Agent.install_from_config(config)
    with pytest.raises(ModelNotFound, match="ghost"):
        Agent.switch_to("ghost")
    assert Agent.active_name() == "a"
    Agent.uninstall()

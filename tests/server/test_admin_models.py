"""/admin/models 端点测试 —— GET 列表 + POST 切换 + 错误路径。

也覆盖 `Agent.switch_to` 的内部行为(虽然路径上是经 controller 的,但这里也直接
对类方法做几个用例,避免回归)。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.server.agent import Agent
from chariot.server.config import ChariotConfig, ConfigError, ModelEntry
from chariot.server.controller import admin_router, register_exception_handlers
from chariot.server.database.session import get_session


@pytest_asyncio.fixture
async def admin_client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """带 admin router + 异常处理器的测试客户端;每用例自管 Agent.install_from_config。"""
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(admin_router, prefix="/admin")

    async def _override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c

    Agent.uninstall()


# ---------- /admin/models GET ----------


async def test_get_models_with_config(admin_client: AsyncClient) -> None:
    config = ChariotConfig(
        models=(
            ModelEntry(name="m1", type="mock", options={}),
            ModelEntry(name="m2", type="mock", options={}),
        ),
        active="m1",
    )
    Agent.install_from_config(config)

    r = await admin_client.get("/admin/models")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] == ["m1", "m2"]
    assert body["active"] == "m1"
    assert "mock" in body["types"]
    assert "anthropic" in body["types"]


async def test_get_models_empty_config(admin_client: AsyncClient) -> None:
    """无 config(MockModel fallback)→ available 空,active 为 null。"""
    Agent.install_from_config(ChariotConfig.empty())

    r = await admin_client.get("/admin/models")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] == []
    assert body["active"] is None
    # types 仍然列出已注册的(mock / anthropic 等)
    assert "mock" in body["types"]


# ---------- /admin/models POST ----------


async def test_post_models_switches_active(admin_client: AsyncClient) -> None:
    config = ChariotConfig(
        models=(
            ModelEntry(name="m1", type="mock", options={}),
            ModelEntry(name="m2", type="mock", options={}),
        ),
        active="m1",
    )
    Agent.install_from_config(config)
    assert Agent.active_name() == "m1"

    r = await admin_client.post("/admin/models", json={"name": "m2"})
    assert r.status_code == 200
    body = r.json()
    assert body["active"] == "m2"
    assert body["model"] == "mock-echo-v1"
    assert Agent.active_name() == "m2"


async def test_post_models_unknown_name_returns_400(
    admin_client: AsyncClient,
) -> None:
    config = ChariotConfig(
        models=(ModelEntry(name="m1", type="mock", options={}),),
        active="m1",
    )
    Agent.install_from_config(config)

    r = await admin_client.post("/admin/models", json={"name": "ghost"})
    assert r.status_code == 400
    assert "ghost" in r.text


async def test_post_models_with_unregistered_type_returns_400(
    admin_client: AsyncClient,
) -> None:
    """name 找到了,但对应 type 未注册 → ConfigError → 400。"""
    config = ChariotConfig(
        models=(ModelEntry(name="weird", type="no_such_type", options={}),),
        active=None,
    )
    Agent.install_from_config(config)

    r = await admin_client.post("/admin/models", json={"name": "weird"})
    assert r.status_code == 400
    assert "no_such_type" in r.text


# ---------- Agent.switch_to 单元测试 ----------


def test_switch_to_updates_active_name() -> None:
    Agent.uninstall()
    config = ChariotConfig(
        models=(
            ModelEntry(name="a", type="mock", options={}),
            ModelEntry(name="b", type="mock", options={}),
        ),
        active="a",
    )
    Agent.install_from_config(config)
    assert Agent.active_name() == "a"

    Agent.switch_to("b")
    assert Agent.active_name() == "b"
    assert Agent.config() is config  # config 引用不变
    Agent.uninstall()


def test_switch_to_unknown_name_raises_config_error() -> None:
    Agent.uninstall()
    config = ChariotConfig(
        models=(ModelEntry(name="a", type="mock", options={}),),
        active="a",
    )
    Agent.install_from_config(config)
    with pytest.raises(ConfigError, match="ghost"):
        Agent.switch_to("ghost")
    # active 不变
    assert Agent.active_name() == "a"
    Agent.uninstall()


# ---------- /admin/models/{name}/probe POST ----------


async def test_probe_mock_entry_returns_ok(admin_client: AsyncClient) -> None:
    """probe 走 mock entry,本地零费用,必返 ok=True。"""
    config = ChariotConfig(
        models=(ModelEntry(name="m1", type="mock", options={}),),
        active="m1",
    )
    Agent.install_from_config(config)

    r = await admin_client.post("/admin/models/m1/probe")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["error"] is None
    assert body["latency_ms"] >= 0


async def test_probe_unknown_name_returns_404(admin_client: AsyncClient) -> None:
    """name 不在 config.models 里 → 404 model_not_found(走 ServiceError 全局 handler)。"""
    config = ChariotConfig(
        models=(ModelEntry(name="m1", type="mock", options={}),),
        active="m1",
    )
    Agent.install_from_config(config)

    r = await admin_client.post("/admin/models/ghost/probe")
    assert r.status_code == 404
    assert "ghost" in r.text


async def test_probe_does_not_affect_active(admin_client: AsyncClient) -> None:
    """probe 是临时 build,不能改 Agent 的 active model。"""
    config = ChariotConfig(
        models=(
            ModelEntry(name="m1", type="mock", options={}),
            ModelEntry(name="m2", type="mock", options={}),
        ),
        active="m1",
    )
    Agent.install_from_config(config)

    r = await admin_client.post("/admin/models/m2/probe")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # active 应仍为 m1,probe 不副作用切换
    assert Agent.active_name() == "m1"

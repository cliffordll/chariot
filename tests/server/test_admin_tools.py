"""/admin/tools 端点测试 —— 0.4.0 内置工具配置。

覆盖:
- GET /admin/tools(seed 后列 4 条 + types + 每条 schema)
- PUT /admin/tools/{name}(toggle enabled / 改 options / 404 / Agent rebuild 生效)
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
from chariot.server.repository.tool_repo import ToolRepo


@pytest_asyncio.fixture
async def admin_client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """seed 4 条 tool fixture + install 空 model config 的 Agent。"""
    await ToolRepo(session).seed_if_empty()
    Agent.install_from_config(ChariotConfig.empty())

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(admin_router, prefix="/admin")

    async def _override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


# ---------- GET ----------


async def test_get_tools_lists_4_fixtures(admin_client: AsyncClient) -> None:
    r = await admin_client.get("/admin/tools")
    assert r.status_code == 200
    body = r.json()
    names = sorted(e["name"] for e in body["entries"])
    assert names == ["http_get", "list_dir", "read_file", "shell_exec"]
    # 全部默认 disabled
    assert all(e["enabled"] is False for e in body["entries"])
    # types 含 4 个
    assert sorted(body["types"]) == ["http_get", "list_dir", "read_file", "shell_exec"]


async def test_get_tools_includes_schema(admin_client: AsyncClient) -> None:
    """每条 tool entry 含 anthropic schema(LLM 看到的形态)。"""
    r = await admin_client.get("/admin/tools")
    body = r.json()
    by_name = {e["name"]: e for e in body["entries"]}
    rf_schema = by_name["read_file"]["schema_"]
    assert rf_schema is not None
    assert rf_schema["name"] == "read_file"
    assert "path" in rf_schema["input_schema"]["required"]


# ---------- PUT update ----------


async def test_put_tool_toggles_enabled(admin_client: AsyncClient, session: AsyncSession) -> None:
    r = await admin_client.put("/admin/tools/read_file", json={"enabled": True})
    assert r.status_code == 200
    assert r.json()["enabled"] is True
    # 持久化:DB 该行 enabled=1
    entry = await ToolRepo(session).get_entry("read_file")
    assert entry is not None
    assert entry.enabled is True


async def test_put_tool_changes_options(admin_client: AsyncClient, session: AsyncSession) -> None:
    new_opts = {"max_bytes": 2048}
    r = await admin_client.put(
        "/admin/tools/read_file",
        json={"options": new_opts},
    )
    assert r.status_code == 200
    assert r.json()["options"] == new_opts
    entry = await ToolRepo(session).get_entry("read_file")
    assert entry is not None
    assert entry.options == new_opts


async def test_put_tool_both_fields(admin_client: AsyncClient) -> None:
    r = await admin_client.put(
        "/admin/tools/shell_exec",
        json={"enabled": True, "options": {"workdir": "/tmp", "timeout_s": 60}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["options"] == {"workdir": "/tmp", "timeout_s": 60}


async def test_put_tool_unknown_name_returns_404(admin_client: AsyncClient) -> None:
    r = await admin_client.put("/admin/tools/ghost", json={"enabled": True})
    assert r.status_code == 404
    assert "tool_not_found" in r.text


async def test_put_tool_enabled_then_appears_in_agent(
    admin_client: AsyncClient,
) -> None:
    """启用 read_file → Agent rebuild → Agent.tools 字典含 read_file。"""
    assert "read_file" not in Agent.current().tools  # 初始全 disabled
    await admin_client.put("/admin/tools/read_file", json={"enabled": True})
    assert "read_file" in Agent.current().tools


async def test_put_tool_invalid_options_rejected(
    admin_client: AsyncClient,
) -> None:
    """options 不通过 Tool.from_config 校验 → rebuild_failed 502。

    read_file 的 max_bytes 必须正整数,传 -1 → repo 写入成功(JSON 有效),
    但 rebuild Tool 时 from_config 抛 ConfigError,_refresh_agent_tools 转 502。
    """
    r = await admin_client.put(
        "/admin/tools/read_file",
        json={"enabled": True, "options": {"max_bytes": -1}},
    )
    assert r.status_code == 502
    assert "rebuild_failed" in r.text

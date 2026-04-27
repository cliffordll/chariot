"""/admin/* 管理端点测试。

v0 架构:server 就是 agent,没有 upstream 概念。admin 只留:
- /admin/ping / /admin/status:基本心跳 + agent model 名
- /admin/logs:请求流水查询(polling since 游标语义)

用 httpx.AsyncClient + ASGITransport 做带 async session 的路由测试;依赖覆盖
`get_session` 直接注入 per-test 的 sqlite session。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.server.agent import Agent
from chariot.server.config import ChariotConfig
from chariot.server.controller import admin_router
from chariot.server.database.session import get_session


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    # /admin/status 会调 Agent.current();测试里初始化一个空 agent(无 entry 也能跑)
    Agent.install_from_config(ChariotConfig.empty())

    app = FastAPI()
    app.include_router(admin_router, prefix="/admin")

    async def _override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c

    Agent.uninstall()


# ---------- ping / status ----------


async def test_ping(client: AsyncClient) -> None:
    r = await client.get("/admin/ping")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


async def test_status(client: AsyncClient) -> None:
    r = await client.get("/admin/status")
    assert r.status_code == 200
    body = r.json()
    assert "version" in body
    assert "uptime_ms" in body
    # 0.3.1:返 entries_count(active 概念退役;空 config 计数 0)
    assert body["entries_count"] == 0
    assert "model" not in body


# ---------- /admin/logs since polling 语义 ----------


async def test_logs_since_strictly_greater(client: AsyncClient, session: AsyncSession) -> None:
    """`?since=T` 只返 created_at > T 的记录(严格大于,为 polling 游标服务)。"""
    from datetime import UTC, datetime, timedelta

    from chariot.server.database.models import LogEntry

    base = datetime.now(UTC).replace(microsecond=0)
    for i, delta in enumerate([0, 10, 20]):  # 三条,间隔 10s
        session.add(
            LogEntry(
                id=f"{i:0>32}",
                model=f"m-{i}",
                status="ok",
                latency_ms=i,
                created_at=base + timedelta(seconds=delta),
            )
        )
    await session.commit()

    # since = 第二条的时间 → 只应拿到第三条(严格大于)
    cutoff = (base + timedelta(seconds=10)).isoformat()
    r = await client.get("/admin/logs", params={"since": cutoff})
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["model"] == "m-2"

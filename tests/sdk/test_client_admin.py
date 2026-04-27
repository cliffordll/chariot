"""ProxyClient admin 方法测试(SDK)。

用 `httpx.MockTransport` 拦截请求,覆盖:
- ping / status
- list_logs(含 since 过滤)
- stats
- shutdown

v0 架构不再有 upstream 概念,对应 admin 方法已删。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest_asyncio

from chariot.sdk.client import ProxyClient


@pytest_asyncio.fixture
async def echo_client() -> AsyncIterator[tuple[ProxyClient, dict[str, Any]]]:
    captured: dict[str, Any] = {"request": None, "response": httpx.Response(200, json={})}

    def _dispatch(req: httpx.Request) -> httpx.Response:
        captured["request"] = req
        return captured["response"]

    transport = httpx.MockTransport(_dispatch)
    http = httpx.AsyncClient(transport=transport)
    client = ProxyClient(http=http, base_url="http://127.0.0.1:12345")
    try:
        yield client, captured
    finally:
        await http.aclose()


# ---------- ping / status ----------


async def test_ping_true(echo_client: tuple[ProxyClient, dict[str, Any]]) -> None:
    client, captured = echo_client
    captured["response"] = httpx.Response(200, json={"ok": True})
    assert await client.ping() is True
    assert captured["request"].url.path == "/admin/ping"


async def test_ping_false_on_non_200(
    echo_client: tuple[ProxyClient, dict[str, Any]],
) -> None:
    client, captured = echo_client
    captured["response"] = httpx.Response(503, json={})
    assert await client.ping() is False


async def test_status(echo_client: tuple[ProxyClient, dict[str, Any]]) -> None:
    client, captured = echo_client
    captured["response"] = httpx.Response(
        200,
        json={
            "version": "0.1.0",
            "uptime_ms": 12345,
            "entries_count": 2,
            "url": "http://127.0.0.1:12345",
        },
    )
    status = await client.status()
    assert status.version == "0.1.0"
    assert status.uptime_ms == 12345
    assert status.entries_count == 2
    assert status.url == "http://127.0.0.1:12345"
    assert captured["request"].url.path == "/admin/status"


# ---------- logs / stats / shutdown ----------


async def test_list_logs_with_filters(
    echo_client: tuple[ProxyClient, dict[str, Any]],
) -> None:
    client, captured = echo_client
    captured["response"] = httpx.Response(200, json=[])
    await client.list_logs(limit=5, offset=10)
    req = captured["request"]
    assert req.url.params["limit"] == "5"
    assert req.url.params["offset"] == "10"


async def test_stats(echo_client: tuple[ProxyClient, dict[str, Any]]) -> None:
    client, captured = echo_client
    captured["response"] = httpx.Response(
        200,
        json={
            "period": "today",
            "since": "2026-04-22T00:00:00+00:00",
            "total_requests": 100,
            "success_rate": 0.95,
            "avg_latency_ms": 412.5,
        },
    )
    stats = await client.stats(period="today")
    assert stats.total_requests == 100
    assert stats.success_rate == 0.95
    req = captured["request"]
    assert req.url.path == "/admin/stats"
    assert req.url.params["period"] == "today"


async def test_shutdown(echo_client: tuple[ProxyClient, dict[str, Any]]) -> None:
    client, captured = echo_client
    captured["response"] = httpx.Response(200, json={"ok": True})
    await client.shutdown()
    req = captured["request"]
    assert req.method == "POST"
    assert req.url.path == "/admin/shutdown"

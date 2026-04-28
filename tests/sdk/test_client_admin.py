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
            "tools_enabled": 0,
            "conversations_count": 0,
            "url": "http://127.0.0.1:12345",
        },
    )
    status = await client.status()
    assert status.version == "0.1.0"
    assert status.uptime_ms == 12345
    assert status.entries_count == 2
    assert status.tools_enabled == 0
    assert status.conversations_count == 0
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


# ---------- conversations(0.4.0)----------

ULID_A = "01JD7K8YQXM2N8R5VF3PCWE4ZB"


async def test_list_conversations(
    echo_client: tuple[ProxyClient, dict[str, Any]],
) -> None:
    client, captured = echo_client
    captured["response"] = httpx.Response(
        200,
        json={
            "items": [
                {
                    "id": ULID_A,
                    "title": "t",
                    "last_model": "mock",
                    "message_count": 3,
                    "created_at": "2026-04-28T10:00:00Z",
                    "updated_at": "2026-04-28T10:05:00Z",
                },
            ],
            "limit": 10,
            "offset": 0,
        },
    )
    resp = await client.list_conversations(limit=10)
    assert len(resp.items) == 1
    assert resp.items[0].id == ULID_A
    req = captured["request"]
    assert req.url.path == "/admin/conversations"
    assert req.url.params["limit"] == "10"


async def test_get_conversation(
    echo_client: tuple[ProxyClient, dict[str, Any]],
) -> None:
    client, captured = echo_client
    captured["response"] = httpx.Response(
        200,
        json={
            "conversation": {
                "id": ULID_A,
                "title": None,
                "last_model": None,
                "message_count": 0,
                "created_at": "2026-04-28T10:00:00Z",
                "updated_at": "2026-04-28T10:00:00Z",
            },
            "messages": [],
        },
    )
    detail = await client.get_conversation(ULID_A)
    assert detail.conversation.id == ULID_A
    assert captured["request"].url.path == f"/admin/conversations/{ULID_A}"


async def test_create_conversation_server_id(
    echo_client: tuple[ProxyClient, dict[str, Any]],
) -> None:
    client, captured = echo_client
    captured["response"] = httpx.Response(
        201,
        json={
            "id": ULID_A,
            "title": "x",
            "last_model": None,
            "message_count": 0,
            "created_at": "2026-04-28T10:00:00Z",
            "updated_at": "2026-04-28T10:00:00Z",
        },
    )
    conv = await client.create_conversation(title="x")
    assert conv.id == ULID_A
    req = captured["request"]
    assert req.method == "POST"
    assert req.url.path == "/admin/conversations"


async def test_create_conversation_with_id(
    echo_client: tuple[ProxyClient, dict[str, Any]],
) -> None:
    client, captured = echo_client
    captured["response"] = httpx.Response(
        201,
        json={
            "id": ULID_A,
            "title": None,
            "last_model": None,
            "message_count": 0,
            "created_at": "2026-04-28T10:00:00Z",
            "updated_at": "2026-04-28T10:00:00Z",
        },
    )
    await client.create_conversation(conv_id=ULID_A)
    import json as _json

    body = _json.loads(captured["request"].content)
    assert body["id"] == ULID_A


async def test_delete_conversation(
    echo_client: tuple[ProxyClient, dict[str, Any]],
) -> None:
    client, captured = echo_client
    captured["response"] = httpx.Response(204)
    await client.delete_conversation(ULID_A)
    req = captured["request"]
    assert req.method == "DELETE"
    assert req.url.path == f"/admin/conversations/{ULID_A}"


async def test_update_conversation_title(
    echo_client: tuple[ProxyClient, dict[str, Any]],
) -> None:
    client, captured = echo_client
    captured["response"] = httpx.Response(
        200,
        json={
            "id": ULID_A,
            "title": "新标题",
            "last_model": None,
            "message_count": 0,
            "created_at": "2026-04-28T10:00:00Z",
            "updated_at": "2026-04-28T10:00:00Z",
        },
    )
    conv = await client.update_conversation(ULID_A, title="新标题")
    assert conv.title == "新标题"
    req = captured["request"]
    assert req.method == "PATCH"


# ---------- tools(0.4.0)----------


async def test_list_tools(echo_client: tuple[ProxyClient, dict[str, Any]]) -> None:
    client, captured = echo_client
    captured["response"] = httpx.Response(
        200,
        json={
            "types": ["read_file", "list_dir", "shell_exec", "http_get"],
            "entries": [
                {
                    "name": "read_file",
                    "type": "read_file",
                    "enabled": False,
                    "options": {"max_bytes": 1048576},
                    "schema_": {
                        "name": "read_file",
                        "description": "...",
                        "input_schema": {},
                    },
                },
            ],
        },
    )
    resp = await client.list_tools()
    assert len(resp.entries) == 1
    assert resp.entries[0].name == "read_file"
    assert captured["request"].url.path == "/admin/tools"


async def test_update_tool(echo_client: tuple[ProxyClient, dict[str, Any]]) -> None:
    client, captured = echo_client
    captured["response"] = httpx.Response(
        200,
        json={
            "name": "read_file",
            "type": "read_file",
            "enabled": True,
            "options": {"max_bytes": 2048},
            "schema_": None,
        },
    )
    tool = await client.update_tool(
        "read_file",
        enabled=True,
        options={"max_bytes": 2048},
    )
    assert tool.enabled is True
    req = captured["request"]
    assert req.method == "PUT"
    assert req.url.path == "/admin/tools/read_file"


# ---------- chat with conversation_id ----------


async def test_post_chat_passes_conversation_header(
    echo_client: tuple[ProxyClient, dict[str, Any]],
) -> None:
    client, captured = echo_client
    captured["response"] = httpx.Response(200, json={"ok": True})
    await client.post_chat({"model": "spy"}, conversation_id=ULID_A)
    req = captured["request"]
    assert req.headers["x-chariot-conversation"] == ULID_A


async def test_post_chat_no_header_when_no_id(
    echo_client: tuple[ProxyClient, dict[str, Any]],
) -> None:
    client, captured = echo_client
    captured["response"] = httpx.Response(200, json={"ok": True})
    await client.post_chat({"model": "spy"})
    req = captured["request"]
    assert "x-chariot-conversation" not in req.headers

"""`ProxyClient` — SDK 主入口:封装 `/admin/*` 调用 + 数据面 POST(流/非流)。

唯一工厂
--------

- `ProxyClient.discover_session()`:async context manager;内部走 `discover()`,
  找到或启动本地 chariot-server,构 httpx client 指向 server。

v0 架构:server 本身就是智能体 (agent),不再有 upstream / BYO-key 概念。
所有数据面请求都走 `/v1/messages` / `/v1/chat/completions` / `/v1/responses`,
body 经 agent 路径生成响应。

Pydantic 模型复用
----------------
admin 相关的 request / response schema 直接从 `chariot.server.controller.*` import;
不在 SDK 这边手写第二份。
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Self, cast

import httpx

from chariot.sdk._adapters import adapter_for
from chariot.sdk.chat import ChatResult
from chariot.sdk.discover import ServerDiscovery
from chariot.server.controller.logs import LogOut
from chariot.server.controller.runtime import StatusResponse
from chariot.server.controller.stats import Period, StatsOut
from chariot.shared.protocols import UPSTREAM_PATH, Protocol

_DATA_TIMEOUT = httpx.Timeout(300.0, connect=10.0)
_ADMIN_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


@dataclass
class ProxyClient:
    """chariot SDK 的 HTTP 客户端;admin + data plane 合二为一。"""

    http: httpx.AsyncClient
    base_url: str
    token: str | None = None

    @classmethod
    @asynccontextmanager
    async def discover_session(
        cls, *, parent_pid: int | None = None, spawn_if_missing: bool = True
    ) -> AsyncIterator[Self]:
        """发现或拉起本地 server,返回连到它的 client。"""
        ep = await ServerDiscovery.find_or_spawn(
            parent_pid=parent_pid, spawn_if_missing=spawn_if_missing
        )
        http = httpx.AsyncClient(timeout=_DATA_TIMEOUT)
        try:
            yield cls(http=http, base_url=ep.url, token=ep.token)
        finally:
            await http.aclose()

    # ---------- admin ----------

    async def ping(self) -> bool:
        resp = await self.http.get(f"{self.base_url}/admin/ping", timeout=_ADMIN_TIMEOUT)
        return resp.status_code == 200

    async def status(self) -> StatusResponse:
        resp = await self.http.get(f"{self.base_url}/admin/status", timeout=_ADMIN_TIMEOUT)
        resp.raise_for_status()
        return StatusResponse.model_validate(resp.json())

    async def list_logs(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        since: datetime | None = None,
    ) -> list[LogOut]:
        """拉请求流水。`since` 作 polling 游标:只返 `created_at > since` 的记录。"""
        params: dict[str, str | int] = {"limit": limit, "offset": offset}
        if since is not None:
            params["since"] = since.isoformat()
        resp = await self.http.get(
            f"{self.base_url}/admin/logs", params=params, timeout=_ADMIN_TIMEOUT
        )
        resp.raise_for_status()
        items = resp.json()
        if not isinstance(items, list):
            raise RuntimeError("GET /admin/logs 返回非 list")
        return [LogOut.model_validate(item) for item in items]  # pyright: ignore[reportUnknownVariableType]

    async def stats(self, *, period: Period = "today") -> StatsOut:
        resp = await self.http.get(
            f"{self.base_url}/admin/stats",
            params={"period": period},
            timeout=_ADMIN_TIMEOUT,
        )
        resp.raise_for_status()
        return StatsOut.model_validate(resp.json())

    async def shutdown(self) -> None:
        """请求 server 优雅关闭;response 返回后不等待实际退出。"""
        resp = await self.http.post(f"{self.base_url}/admin/shutdown", timeout=_ADMIN_TIMEOUT)
        resp.raise_for_status()

    # ---------- data plane ----------

    def _data_url(self, fmt: Protocol) -> str:
        return f"{self.base_url}{UPSTREAM_PATH[fmt]}"

    async def post_chat(
        self,
        fmt: Protocol,
        body: dict[str, Any],
    ) -> httpx.Response:
        """非流式数据面 POST;调用方拿到 Response 自己 `.json()`。"""
        return await self.http.post(
            self._data_url(fmt),
            json=body,
            headers={"content-type": "application/json"},
        )

    @asynccontextmanager
    async def stream_chat(
        self,
        fmt: Protocol,
        body: dict[str, Any],
    ) -> AsyncIterator[httpx.Response]:
        """流式数据面 POST;返回 async context,`resp.aiter_bytes()` 读流。"""
        req = self.http.build_request(
            "POST",
            self._data_url(fmt),
            json=body,
            headers={"content-type": "application/json"},
        )
        resp = await self.http.send(req, stream=True)
        try:
            yield resp
        finally:
            await resp.aclose()

    async def chat_once(
        self,
        text: str,
        *,
        model: str,
        fmt: Protocol = Protocol.MESSAGES,
        max_tokens: int = 1024,
    ) -> ChatResult:
        """发一条消息,非流式,返回 `ChatResult`。

        简单用例的便捷入口;更复杂场景用 `post_chat` / `stream_chat` 自己拼。
        流式渲染走 `stream_chat` + `ChatStream(fmt).text_deltas(resp)`。
        """
        adapter = adapter_for(fmt)
        body = adapter.build_request_body(text, model=model, max_tokens=max_tokens, stream=False)

        t0 = time.monotonic()
        resp = await self.post_chat(fmt, body)
        latency_ms = int((time.monotonic() - t0) * 1000)
        resp.raise_for_status()

        data: Any = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"响应顶层不是对象: {type(data).__name__}")

        return ChatResult.from_response_data(
            cast(dict[str, Any], data),
            adapter=adapter,
            fmt=fmt,
            server_base_url=self.base_url,
            latency_ms=latency_ms,
        )

"""`ProxyClient` — SDK 主入口:封装 `/admin/*` 调用 + 数据面 POST(流/非流)。

唯一工厂
--------

- `ProxyClient.discover_session()`:async context manager;内部走 `discover()`,
  找到或启动本地 chariot-server,构 httpx client 指向 server。

0.2.0 架构:server 本身就是智能体 (agent),不再有 upstream / BYO-key 概念;
单协议对外只接 `POST /v1/messages`(Anthropic Messages)。OpenAI 客户端通过外部
转换器(LiteLLM 等)接入,SDK 这边不再需要 protocol 维度。

Pydantic 模型复用
----------------
admin 相关的 request / response schema 直接从 `chariot.server.controller.*` import;
不在 SDK 这边手写第二份。
"""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Self, cast

import httpx

from chariot.sdk.chat import ChatResult
from chariot.sdk.discover import ServerDiscovery
from chariot.server.controller.logs import LogOut
from chariot.server.controller.models import (
    EntryResponse,
    ModelsListResponse,
    ProbeResult,
    SwitchModelResponse,
)
from chariot.server.controller.runtime import StatusResponse
from chariot.server.controller.stats import Period, StatsOut

_DATA_TIMEOUT = httpx.Timeout(300.0, connect=10.0)
_ADMIN_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
# 探针要等真上游响应,比 admin 操作宽,但比 chat 短(只发 1 token)
_PROBE_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_MESSAGES_PATH = "/v1/messages"


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
    ) -> AsyncGenerator[Self]:
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

    async def list_models(self) -> ModelsListResponse:
        """列出可用 model + 当前 active + 已注册 type。"""
        resp = await self.http.get(f"{self.base_url}/admin/models", timeout=_ADMIN_TIMEOUT)
        resp.raise_for_status()
        return ModelsListResponse.model_validate(resp.json())

    async def use_model(self, name: str) -> SwitchModelResponse:
        """切换 active model。失败(name 未配 / type 未注册)→ httpx.HTTPStatusError(400)。"""
        resp = await self.http.post(
            f"{self.base_url}/admin/models",
            json={"name": name},
            timeout=_ADMIN_TIMEOUT,
        )
        resp.raise_for_status()
        return SwitchModelResponse.model_validate(resp.json())

    async def probe_model(self, name: str) -> ProbeResult:
        """对指定 model 跑一次探针(发 1 条最小请求验通断)。

        返回 `ProbeResult(ok, latency_ms, error?)`;ok=False 时 error 透传上游错因
        (`upstream_auth_failed` / `upstream_unreachable` / 配置错 ...)。
        name 不存在 → httpx.HTTPStatusError(404);其它情况一律包成 ProbeResult。

        **真打上游一次,产生 ~1 token 费用**(MockModel 走本地零费用)。
        """
        resp = await self.http.post(
            f"{self.base_url}/admin/models/{name}/probe",
            timeout=_PROBE_TIMEOUT,
        )
        resp.raise_for_status()
        return ProbeResult.model_validate(resp.json())

    # ---- entries CRUD(0.3.0)----

    async def create_model(
        self,
        *,
        name: str,
        type: str,
        options: dict[str, Any] | None = None,
    ) -> EntryResponse:
        """新增 model entry。重名 → 409;type 未注册 → 400。"""
        resp = await self.http.post(
            f"{self.base_url}/admin/models/entries",
            json={"name": name, "type": type, "options": options or {}},
            timeout=_ADMIN_TIMEOUT,
        )
        resp.raise_for_status()
        return EntryResponse.model_validate(resp.json())

    async def update_model(
        self,
        name: str,
        *,
        type: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> EntryResponse:
        """更新 type / options。改 active entry 时 server 端会自动 rebuild model 实例。"""
        body: dict[str, Any] = {}
        if type is not None:
            body["type"] = type
        if options is not None:
            body["options"] = options
        resp = await self.http.put(
            f"{self.base_url}/admin/models/entries/{name}",
            json=body,
            timeout=_ADMIN_TIMEOUT,
        )
        resp.raise_for_status()
        return EntryResponse.model_validate(resp.json())

    async def delete_model(self, name: str) -> None:
        """删 entry。active 不许删 → 400(`cannot_delete_active`)。"""
        resp = await self.http.delete(
            f"{self.base_url}/admin/models/entries/{name}",
            timeout=_ADMIN_TIMEOUT,
        )
        resp.raise_for_status()

    async def duplicate_model(
        self,
        name: str,
        *,
        as_name: str | None = None,
    ) -> EntryResponse:
        """复制 entry。`as_name` 缺省 `<name>_copy`,碰撞自动 `_copy_N`。"""
        body: dict[str, Any] = {}
        if as_name is not None:
            body["as"] = as_name
        resp = await self.http.post(
            f"{self.base_url}/admin/models/entries/{name}/duplicate",
            json=body,
            timeout=_ADMIN_TIMEOUT,
        )
        resp.raise_for_status()
        return EntryResponse.model_validate(resp.json())

    # ---------- data plane ----------

    @property
    def _data_url(self) -> str:
        return f"{self.base_url}{_MESSAGES_PATH}"

    async def post_chat(self, body: dict[str, Any]) -> httpx.Response:
        """非流式数据面 POST;调用方拿到 Response 自己 `.json()`。"""
        return await self.http.post(
            self._data_url,
            json=body,
            headers={"content-type": "application/json"},
        )

    @asynccontextmanager
    async def stream_chat(self, body: dict[str, Any]) -> AsyncGenerator[httpx.Response]:
        """流式数据面 POST;返回 async context,`resp.aiter_bytes()` 读流。"""
        req = self.http.build_request(
            "POST",
            self._data_url,
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
        max_tokens: int = 1024,
    ) -> ChatResult:
        """发一条消息,非流式,返回 `ChatResult`。

        简单用例的便捷入口;更复杂场景用 `post_chat` / `stream_chat` 自己拼 body。
        流式渲染走 `stream_chat(body)` + `ChatStream().text_deltas(resp)`。
        """
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": text}],
        }

        t0 = time.monotonic()
        resp = await self.post_chat(body)
        latency_ms = int((time.monotonic() - t0) * 1000)
        resp.raise_for_status()

        data: Any = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"响应顶层不是对象: {type(data).__name__}")

        return ChatResult.from_response_data(
            cast(dict[str, Any], data),
            server_base_url=self.base_url,
            latency_ms=latency_ms,
        )

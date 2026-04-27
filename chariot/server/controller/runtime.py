"""admin 运维控制:/admin/ping、/admin/status、/admin/shutdown。

三者都是"对 server 进程自身的控制/探测",没有业务语义,合在一个文件便于维护。

- `/admin/ping`:最轻量健康检查(不进 DB)
- `/admin/status`:版本 + 启动时长 + entries count(用于 CLI `chariot status`)
- `/admin/shutdown`:触发 uvicorn graceful shutdown
  - 响应先发,后台任务再置 `server.should_exit = True`
  - CLI `chariot stop` 优先调这里;兜底用 psutil.kill(pid)
"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Request
from pydantic import BaseModel

from chariot import __version__
from chariot.server.agent import Agent

router = APIRouter()

_START_MONO = time.monotonic()


# ---------- /admin/ping ----------


class PingResponse(BaseModel):
    ok: bool


@router.get("/ping", response_model=PingResponse)
async def ping() -> PingResponse:
    return PingResponse(ok=True)


# ---------- /admin/status ----------


class StatusResponse(BaseModel):
    version: str
    uptime_ms: int
    entries_count: int  # 0.3.1:已注册 entries 数量;active 概念退役后不再返单一 model
    url: str  # 客户端抵达 server 的 base URL(含 scheme + host + port)


@router.get("/status", response_model=StatusResponse)
async def status(request: Request) -> StatusResponse:
    uptime_ms = int((time.monotonic() - _START_MONO) * 1000)
    agent = Agent.current()
    # 直接从 ASGI scope["server"] = (host, port) 拿 bind 的地址;避开 base_url
    # 依赖 Host header(vite proxy 会改 host,某些代理链路会让 base_url 为空)。
    # chariot loopback-only 固定 http,不担心 scheme 推错
    scope_server = request.scope.get("server") or (None, None)
    host, port = scope_server
    url = f"http://{host}:{port}" if host and port else str(request.base_url).rstrip("/")
    return StatusResponse(
        version=__version__,
        uptime_ms=uptime_ms,
        entries_count=len(agent.models),
        url=url,
    )


# ---------- /admin/shutdown ----------


class ShutdownResponse(BaseModel):
    ok: bool


@router.post("/shutdown", response_model=ShutdownResponse)
async def shutdown(request: Request) -> ShutdownResponse:
    """触发 uvicorn 的优雅关闭流程;**响应先发,后关**。"""
    server = getattr(request.app.state, "uvicorn_server", None)

    async def _trigger() -> None:
        # 让当前响应先回到客户端,再 yield 给 uvicorn 去设置 should_exit
        await asyncio.sleep(0.05)
        if server is not None:
            server.should_exit = True

    # 后台触发,不阻塞响应
    asyncio.create_task(_trigger())  # noqa: RUF006 — 即发即忘,关闭不需等待
    return ShutdownResponse(ok=True)

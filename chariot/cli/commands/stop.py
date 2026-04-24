"""`chariot stop` — 请 server 优雅退出,超时再 kill。"""

from __future__ import annotations

import asyncio
import contextlib
import time

import psutil
import typer

from chariot.cli.core.render import Renderer
from chariot.sdk.client import ProxyClient
from chariot.server.runtime.endpoint import EndpointFile

_SHUTDOWN_WAIT_SEC = 5.0
_POLL_INTERVAL_SEC = 0.1


def stop_cmd() -> None:
    asyncio.run(_run())


async def _run() -> None:
    ep = EndpointFile.read()
    if ep is None:
        Renderer.out("not running")
        return

    pid = ep.pid
    # 优雅关
    with contextlib.suppress(Exception):
        async with ProxyClient.discover_session(spawn_if_missing=False) as client:
            await client.shutdown()

    deadline = time.monotonic() + _SHUTDOWN_WAIT_SEC
    while time.monotonic() < deadline:
        if not psutil.pid_exists(pid):
            Renderer.out("server stopped")
            return
        await asyncio.sleep(_POLL_INTERVAL_SEC)

    # 超时兜底 kill
    with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
        psutil.Process(pid).kill()
    Renderer.out(f"server stopped (forced kill pid {pid})")


def register(app: typer.Typer) -> None:
    app.command("stop", help="请求 server 优雅退出(超时强杀)")(stop_cmd)

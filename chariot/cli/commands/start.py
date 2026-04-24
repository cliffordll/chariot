"""`chariot start` — 确保 server 在后台跑着。

与 SDK `discover()` 不同:spawn 的 server **不**绑定 CLI 进程为 parent,
让它在 CLI 退出后继续存活(真后台化)。这样后续 `chariot status` / `upstream`
等命令才能连到同一个 server。

原语来自 `chariot.sdk.discover.ServerDiscovery`(check_existing / spawn /
wait_until_ready / ping),本文件只负责组装 + 面向用户的进度打印。
"""

from __future__ import annotations

import asyncio
import time

import psutil
import typer

from chariot.cli.core.render import Renderer
from chariot.sdk.discover import ServerDiscovery
from chariot.server.runtime.endpoint import EndpointFile
from chariot.server.runtime.lockfile import SpawnLock


def start_cmd() -> None:
    """后台启动 chariot-server(若未跑),立即返回。"""
    asyncio.run(_run())


async def _run() -> None:
    # 已跑就直接报告
    ep = await ServerDiscovery.check_existing()
    if ep is not None:
        Renderer.out(f"already running at {ep.url} (pid {ep.pid})")
        return

    # check_existing 已清陈旧文件;但它的 None 分支也包括"PID 活但 ping 不通",
    # 此时 endpoint.json 可能仍在,顺手再清一次避免下一步轮询读到误导数据
    stale = EndpointFile.read()
    if stale is not None and not psutil.pid_exists(stale.pid):
        Renderer.out(f"delete stale endpoint {stale.url} (pid {stale.pid})")
        EndpointFile.delete()

    try:
        Renderer.out("acquiring spawn lock...")
        lock_fd = SpawnLock.acquire()
    except FileExistsError:
        Renderer.die("另一个进程正在启动 server,请稍后再试")
        return  # for type checker

    try:
        ServerDiscovery.spawn(parent_pid=None)  # None = 独立 daemon,不绑 CLI
        Renderer.out("spawned detached server process, waiting for it to be ready...")

        deadline = time.monotonic() + ServerDiscovery.WAIT_TIMEOUT_SEC
        ep = await ServerDiscovery.wait_until_ready(deadline=deadline)
        if ep is None:
            Renderer.die(f"server {ServerDiscovery.WAIT_TIMEOUT_SEC}s 内未就绪")
            return
        Renderer.out(f"server started on {ep.url} (pid {ep.pid})")
    finally:
        SpawnLock.release(lock_fd)
        Renderer.out("released spawn lock")


def register(app: typer.Typer) -> None:
    app.command("start", help="后台启动 server(若未跑)")(start_cmd)

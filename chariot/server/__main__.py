"""python -m chariot.server 入口。

时序:
    1. 抢 spawn.lock(失败 → exit 0)
    2. 起 uvicorn.Server task
    3. 轮询 server.started(10s 超时)
    4. 读 bound port,写 endpoint.json(`.tmp` → rename 原子)
    5. 放 spawn.lock(这时客户端可见有效的 endpoint.json)
    6. 起 watch_parent 协程(若传了 --parent-pid)
    7. await server 退出
    finally: 删 endpoint.json + 兜底放 lock
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import secrets
import sys
import time

import psutil
import uvicorn

from chariot.server.app import create_app
from chariot.server.runtime.endpoint import EndpointFile
from chariot.server.runtime.logger import configure_logging
from chariot.server.runtime.watcher import watch_parent

_log = logging.getLogger("chariot.server")

_UVICORN_STARTUP_TIMEOUT_SEC = 10.0
_UVICORN_GRACEFUL_SHUTDOWN_SEC = 30


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="chariot-server", description="chariot LLM 代理 server")
    parser.add_argument(
        "--parent-pid",
        type=int,
        default=None,
        help="父进程 PID;父死后本 server 自动优雅退出",
    )
    return parser.parse_args()


async def _wait_started(server: uvicorn.Server, serve_task: asyncio.Task[None]) -> None:
    """等 uvicorn bind 完 socket,或启动失败/超时。"""
    deadline = time.monotonic() + _UVICORN_STARTUP_TIMEOUT_SEC
    while not server.started:
        if serve_task.done():
            # 启动时就挂了 → 让异常冒出来
            await serve_task
            raise RuntimeError("uvicorn 启动失败,serve() 提前返回")
        if time.monotonic() > deadline:
            raise RuntimeError(f"uvicorn {_UVICORN_STARTUP_TIMEOUT_SEC}s 内没起来")
        await asyncio.sleep(0.05)


def _read_bound_url(server: uvicorn.Server) -> str:
    """从 server.servers 读出已 bind 的 127.0.0.1 地址。"""
    sockets = server.servers[0].sockets
    sockaddr = sockets[0].getsockname()
    host = str(sockaddr[0])
    port = int(sockaddr[1])
    return f"http://{host}:{port}"


async def _amain(args: argparse.Namespace) -> int:
    # 并发保护:只靠 endpoint.json + pid 活检;spawn.lock 由客户端(CLI/SDK)持有,
    # server 自身不抢(否则客户端持锁时子进程 server 会抢不到 → 假冒"another instance"
    # 退出 0,把 CLI 的轮询卡到超时。阶段 4.2 调试定位)
    ep = EndpointFile.read()
    if ep is not None:
        if psutil.pid_exists(ep.pid):
            _log.info(
                "another server already running at %s (pid %d), exiting cleanly",
                ep.url,
                ep.pid,
            )
            return 0
        # 陈旧 endpoint.json(pid 已死) → 清掉继续
        _log.info("stale endpoint.json found (pid %d dead), cleaning up", ep.pid)
        EndpointFile.delete()

    endpoint_written = False
    watcher_task: asyncio.Task[None] | None = None

    app = create_app()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=0,
        # logger 由 configure_logging() 托管,uvicorn 不自己装;access_log 关,
        # dataplane 请求改为由 forwarder 走 logger + logs 表双通道
        log_config=None,
        access_log=False,
        timeout_graceful_shutdown=_UVICORN_GRACEFUL_SHUTDOWN_SEC,
    )
    server = uvicorn.Server(config)
    # 给 /admin/shutdown 端点访问;shutdown 端点靠 server.should_exit=True 优雅关
    app.state.uvicorn_server = server
    serve_task = asyncio.create_task(server.serve())

    try:
        await _wait_started(server, serve_task)

        url = _read_bound_url(server)
        token = secrets.token_urlsafe(32)
        EndpointFile.write(url=url, token=token, pid=os.getpid())
        endpoint_written = True

        _log.info("chariot-server listening on %s (pid=%d)", url, os.getpid())

        if args.parent_pid is not None:
            _log.info("watching parent pid %d", args.parent_pid)
            watcher_task = asyncio.create_task(watch_parent(args.parent_pid, server))

        await serve_task
        return 0
    finally:
        if watcher_task is not None and not watcher_task.done():
            watcher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher_task
        if endpoint_written:
            EndpointFile.delete()


def main() -> None:
    configure_logging()
    args = _parse_args()
    try:
        exit_code = asyncio.run(_amain(args))
    except KeyboardInterrupt:
        _log.info("received KeyboardInterrupt, exiting")
        exit_code = 0
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

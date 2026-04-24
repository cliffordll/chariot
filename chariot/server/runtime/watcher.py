"""父进程 PID 监护 + 优雅关闭。

每 3s 探一次父 PID,死了就 set `server.should_exit`;
uvicorn 自身处理停接新连接 + 等 in-flight 超时(30s) + 关 TCP。
"""

from __future__ import annotations

import asyncio

import psutil
import uvicorn

_POLL_INTERVAL_SEC = 3.0


async def watch_parent(parent_pid: int, server: uvicorn.Server) -> None:
    """轮询父 PID,死了触发 server 优雅关闭。"""
    while True:
        await asyncio.sleep(_POLL_INTERVAL_SEC)
        if not psutil.pid_exists(parent_pid):
            await graceful_shutdown(server)
            return


async def graceful_shutdown(server: uvicorn.Server) -> None:
    """DESIGN §6 五步的落地版。

    - 1 停接新连接 → 下面这行
    - 2 等 in-flight → uvicorn `timeout_graceful_shutdown`(见 `__main__.py` 配置)
    - 3 超时关 TCP → uvicorn 内置
    - 4 flush logs 队列 → v0 日志同步,无队列,跳过
    - 5 删 endpoint.json → `__main__.py` 的 finally 负责
    """
    server.should_exit = True

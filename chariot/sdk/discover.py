"""SDK 侧发现 / 启动 chariot-server。

`ServerDiscovery` 把所有相关逻辑收齐:
- **原语**(classmethod):`ping` / `check_existing` / `spawn` / `wait_until_ready`
- **编排**(classmethod):`find_or_spawn(...)` —— 找到已有 server 或自己 spawn 一个,
  返回 **已 ping 通** 的 Endpoint

外部调用永远是 `ServerDiscovery.<method>()` —— 模块级不暴露自由函数 / 可变变量。

流程(`find_or_spawn`)
----------------------
1. 读 `~/.chariot/endpoint.json`
   - 不存在 → 进入 spawn 分支
   - 存在但 PID 已死 → 删掉 endpoint.json,进入 spawn 分支
   - 存在且 PID 活 → 直接复用(ping 失败才当作死)
2. spawn:先抢 `spawn.lock`
   - 抢到 → 自己 `python -m chariot.server --parent-pid <caller>` detach 启动,
     然后轮询 endpoint.json + /admin/ping 直到就绪或超时
   - 抢不到 → 说明别人在 spawn,转为只轮询 endpoint.json + ping(最多 10s)
3. 返回可用的 `Endpoint`

与 server 侧 `runtime/lockfile.py` / `runtime/endpoint.py` 的实现对称。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from typing import ClassVar

import httpx
import psutil

from chariot.server.runtime.endpoint import EndpointBase, EndpointFile
from chariot.server.runtime.lockfile import SpawnLock


class ServerDiscovery:
    """chariot-server 发现 + spawn + 就绪轮询;所有接口都是 classmethod,不实例化。"""

    # 启动 server 的命令行:用 `python -m chariot.server` 不依赖 exe(PyInstaller
    # 打包前后都能跑)
    _SPAWN_CMD: ClassVar[list[str]] = [sys.executable, "-m", "chariot.server"]

    # 轮询总超时;公共(cli/start.py 组装自己的流程时也读这个值)
    WAIT_TIMEOUT_SEC: ClassVar[float] = 10.0

    # 单次 sleep 间隔 / /admin/ping HTTP 超时(内部用)
    _POLL_INTERVAL_SEC: ClassVar[float] = 0.1
    _PING_TIMEOUT_SEC: ClassVar[float] = 1.0

    # ---------- 原子原语 ----------

    @classmethod
    async def ping(cls, url: str) -> bool:
        """短超时 ping /admin/ping;失败(含连接失败)返回 False。"""
        try:
            async with httpx.AsyncClient(timeout=cls._PING_TIMEOUT_SEC) as client:
                resp = await client.get(f"{url}/admin/ping")
                return resp.status_code == 200
        except httpx.HTTPError:
            return False

    @classmethod
    async def check_existing(cls) -> EndpointBase | None:
        """读 endpoint.json + PID 活性 + /admin/ping;任一失败返 None 并清陈旧文件。"""
        ep = EndpointFile.read()
        if ep is None:
            return None
        if not psutil.pid_exists(ep.pid):
            EndpointFile.delete()
            return None
        if not await cls.ping(ep.url):
            return None
        return ep

    @classmethod
    def spawn(cls, *, parent_pid: int | None = None) -> None:
        """后台启动 server,与当前进程 detach(关 stdio,不做 wait)。

        - `parent_pid=None`:不拼 `--parent-pid`,server 独立存活(`chariot start`
          语义:CLI 退出后 server 继续)
        - `parent_pid=<pid>`:绑 caller 生命周期,caller 挂后 5s 内 graceful_shutdown
          (SDK `find_or_spawn()` 语义)

        平台特定:
        - Windows:`creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`
        - POSIX:`start_new_session=True`(脱离 caller 控制终端)
        """
        cmd = list(cls._SPAWN_CMD)
        if parent_pid is not None:
            cmd.extend(["--parent-pid", str(parent_pid)])

        if sys.platform == "win32":
            # DETACHED_PROCESS = 0x00000008;CREATE_NEW_PROCESS_GROUP = 0x00000200
            subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=0x00000008 | 0x00000200,
            )
        else:
            subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )

    @classmethod
    async def wait_until_ready(cls, *, deadline: float) -> EndpointBase | None:
        """轮询直到 endpoint.json 出现且 ping 通;到 deadline 仍未就绪返回 None。"""
        while time.monotonic() < deadline:
            ep = EndpointFile.read()
            if ep is not None and psutil.pid_exists(ep.pid) and await cls.ping(ep.url):
                return ep
            await asyncio.sleep(cls._POLL_INTERVAL_SEC)
        return None

    # ---------- 编排 ----------

    @classmethod
    async def find_or_spawn(
        cls,
        *,
        parent_pid: int | None = None,
        spawn_if_missing: bool = True,
    ) -> EndpointBase:
        """返回一个 **已 ping 通** 的 Endpoint。

        - `parent_pid`:spawn 时传给 server 的 `--parent-pid`;None → 当前进程
        - `spawn_if_missing`:False 时,若 endpoint 不存在 / 不可达直接 raise
          `RuntimeError`,不启动新 server(测试 / 只读场景用)
        """
        ep = await cls.check_existing()
        if ep is not None:
            return ep

        if not spawn_if_missing:
            raise RuntimeError("no running chariot-server (endpoint.json 不存在或不可达)")

        effective_parent = parent_pid if parent_pid is not None else os.getpid()

        spawned_ourselves = False
        lock_fd: int | None = None
        try:
            try:
                lock_fd = SpawnLock.acquire()
                spawned_ourselves = True
            except FileExistsError:
                lock_fd = None  # 别人在 spawn,走"只轮询"分支

            if spawned_ourselves:
                cls.spawn(parent_pid=effective_parent)

            ep = await cls.wait_until_ready(deadline=time.monotonic() + cls.WAIT_TIMEOUT_SEC)
            if ep is None:
                raise RuntimeError(
                    f"chariot-server {cls.WAIT_TIMEOUT_SEC}s 内未就绪"
                    f"({'自 spawn' if spawned_ourselves else '等待别人 spawn'})"
                )
            return ep
        finally:
            # 仅当自己抢到锁才 release(server 启动成功会自释,客户端兜底以防启动失败卡锁)
            if spawned_ourselves and lock_fd is not None:
                SpawnLock.release(lock_fd)

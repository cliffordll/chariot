"""Sidecar 进程入口:`python -m chariot.sidecar`(0.6.5 S.8b)。

由 Tauri 主进程 spawn,通过 stdin/stdout 双向 stdio JSON-RPC 跟前端通信。
跟 CLI 的 `chariot` 命令不同:CLI 是用户面 typer,sidecar 是机器面 RPC。

启动顺序
--------
1. logging → stderr(stdout 是 RPC 通道,污染了前端解析就崩)
2. AgentRegistry.reserve("sidecar", db_path=...) 装载 AIAgent
3. JsonRpcServer + register_methods 注册业务 method
4. `StdioBridge` 起 stdin pump 线程 + 准备 sync stdout writer
5. server.serve(reader, writer) — 主循环,EOF break

退出
----
- stdin EOF(Tauri 关 stdin) → pump 线程退出 + reader.feed_eof()
  → server.serve 自然 return
- finally:AgentRegistry.clear → ClientCache.aclose_all → dispose_db
- **不主动 kill**(对齐 CLAUDE.md sidecar 生命周期约定)

平台
----
**不能** 用 `asyncio.connect_read_pipe(stdin)`,理由:Windows ProactorEventLoop
把 stdin 注册进 IOCP 时,console 或 anonymous pipe(PowerShell `|`、Tauri spawn
子进程)都会失败:`OSError: [WinError 6] 句柄无效`。这是 asyncio 在 Windows
上长期已知限制 —— 只有 named pipe / socket 支持 IOCP attach。

绕过方案(`StdioBridge`):**stdin 走守护线程同步 readline,push 进 asyncio
StreamReader;stdout 走同步 write + flush**。业务循环仍是 asyncio,但 I/O 不
碰 asyncio。跟 hermes-agent 的 `tui_gateway/transport.py` 走同样模式。
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading

from chariot.agent.registry import AgentRegistry
from chariot.database.session import DEFAULT_DB_PATH, dispose_db
from chariot.providers.clients import ClientCache
from chariot.rpc.jsonrpc import JsonRpcServer
from chariot.sidecar.methods import register_methods

# sidecar 单 client 单 session;固定 session_key = "sidecar"
_SESSION_KEY = "sidecar"


class _StdoutWriter:
    """同步 stdout writer,满足 `chariot.rpc.jsonrpc._Writer` Protocol。

    `write(bytes)` 直接 sync 写 + flush;`async drain()` 是 no-op(write 已经
    flush)。绕开 Windows ProactorEventLoop 对 console / anonymous pipe stdout
    的限制,跟 stdin pump 配合做"业务 async,I/O sync"。

    并发:JsonRpcServer 内部用 `asyncio.Lock` 保护写路径,这里不再加额外锁
    (writer 只被主 asyncio 线程调,不跨线程)。
    """

    def write(self, data: bytes) -> None:
        """sync 写 stdout,write 后立即 flush(避免管道缓冲让前端等帧)。"""
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()

    async def drain(self) -> None:
        # write() 已 flush,无 async-side flush 需求
        pass


class StdioBridge:
    """跨平台 stdio I/O bridge:线程读 stdin → asyncio.StreamReader;sync 写 stdout。

    生命周期:
    - `__init__`:创建 reader(asyncio.StreamReader)+ writer(_StdoutWriter)+ thread 占位
    - `start()`:起 daemon 线程跑 `_pump`(必须在 asyncio loop running 时调,
      因 _pump 用 `loop.call_soon_threadsafe` push 数据进 reader)
    - 主循环跑 `JsonRpcServer.serve(bridge.reader, bridge.writer)` 即可
    - stdin EOF → pump 线程读到 b'' → break + `feed_eof()` → reader 给 EOF
      → server.serve 自然 return
    - 进程退出时 daemon 线程自动死,无需显式 join

    线程安全:
    - pump 线程只调 `call_soon_threadsafe` push 数据;asyncio loop 在主线程
      处理。reader 接 push 是线程安全的(call_soon_threadsafe 保证)
    """

    def __init__(self) -> None:
        self.reader: asyncio.StreamReader = asyncio.StreamReader()
        self.writer: _StdoutWriter = _StdoutWriter()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """起 daemon 线程跑 stdin pump。loop 必须已 running。"""
        loop = asyncio.get_running_loop()
        self._thread = threading.Thread(
            target=self._pump,
            args=(loop,),
            daemon=True,
            name="sidecar-stdin-pump",
        )
        self._thread.start()

    def _pump(self, loop: asyncio.AbstractEventLoop) -> None:
        """[在线程跑] 同步读 stdin → call_soon_threadsafe push 进 reader。

        `sys.stdin.buffer.readline()` 在 Windows / POSIX 上对 console / pipe /
        重定向都正常工作,EOF 返 `b''`。
        """
        try:
            while True:
                line = sys.stdin.buffer.readline()
                if not line:
                    break  # EOF
                loop.call_soon_threadsafe(self.reader.feed_data, line)
        finally:
            loop.call_soon_threadsafe(self.reader.feed_eof)


async def serve_stdio() -> None:
    """sidecar 主循环:装载 → register → serve → cleanup。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    log = logging.getLogger(__name__)
    log.info("sidecar starting")
    try:
        agent = await AgentRegistry.reserve(_SESSION_KEY, db_path=DEFAULT_DB_PATH)
        log.info(
            "AIAgent loaded (providers=%s, tools=%s)",
            list(agent.providers),
            list(agent.tools),
        )

        server = JsonRpcServer()
        register_methods(server, agent, db_path=DEFAULT_DB_PATH)
        log.info("registered RPC methods: %s", sorted(server.known_methods()))

        bridge = StdioBridge()
        bridge.start()
        log.info("serving stdio JSON-RPC")
        await server.serve(bridge.reader, bridge.writer)
        log.info("stdin EOF, shutting down")
    finally:
        await AgentRegistry.clear()
        await ClientCache.aclose_all()
        await dispose_db()
        log.info("sidecar exit")


if __name__ == "__main__":
    asyncio.run(serve_stdio())

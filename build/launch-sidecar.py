"""PyInstaller 入口壳:`chariot-sidecar.exe`。

单独放一个 launcher(而不是直接把 `chariot/sidecar/__main__.py` 作 PyInstaller
入口),避免 `__main__.py` 作 script 运行时的 `__name__ == "__main__"` 与包
import 路径歧义。

由 Tauri 主进程 spawn,通过 stdin/stdout 双向 stdio JSON-RPC 跟前端通信;
stdin EOF = 自然退出。
"""

from __future__ import annotations

import asyncio

from chariot.sidecar.__main__ import serve_stdio

if __name__ == "__main__":
    asyncio.run(serve_stdio())

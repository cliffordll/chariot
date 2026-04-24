"""PyInstaller 入口壳:`chariot-server.exe`。

单独放一个 launcher(而不是直接把 `chariot/server/__main__.py` 作 PyInstaller 入口),
避免 `__main__.py` 作 script 运行时的 `__name__ == "__main__"` 与包 import 路径歧义。
"""

from __future__ import annotations

from chariot.server.__main__ import main

if __name__ == "__main__":
    main()

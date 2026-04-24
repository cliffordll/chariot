"""server 端 logging 配置。

启动时调一次 `configure_logging()` 装好根 logger + uvicorn 子 logger。
格式:`<asctime> <levelname> [<name>] <msg>`;级别由 `CHARIOT_LOG_LEVEL`
环境变量控制(默认 INFO)。

本模块不提供 file handler —— 持久化走 DB(`logs` 表 + `/admin/logs`),
避免重复记录 + GUI 的"看日志"入口只认一份数据源。
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys


def configure_logging() -> None:
    """装 root logger 的 stdout handler + 统一格式。多次调用幂等。"""
    level = _parse_level(os.environ.get("CHARIOT_LOG_LEVEL"))
    root = logging.getLogger()
    # 幂等:已装过就只改级别,不重复加 handler
    if any(getattr(h, "_chariot_tag", False) for h in root.handlers):
        root.setLevel(level)
        return

    # Windows 默认 stdout 用 cp1252,中文日志会触发 UnicodeEncodeError;
    # Python 3.7+ 支持 reconfigure,把 stdout 提升到 utf-8 兜底。某些 stream
    # (pipe / pytest capture buffer)不支持 reconfigure,忽略异常即可
    stream = sys.stdout
    with contextlib.suppress(Exception):
        stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handler._chariot_tag = True  # type: ignore[attr-defined]  # 标记幂等
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn 自带的 access / error logger 走我们的 handler;
    # access 默认 INFO,吵则可通过 CHARIOT_ACCESS_LEVEL 提到 WARNING
    access_level = _parse_level(os.environ.get("CHARIOT_ACCESS_LEVEL"), default="INFO")
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
        lg.setLevel(level)
    uv_access = logging.getLogger("uvicorn.access")
    uv_access.handlers.clear()
    uv_access.propagate = True
    uv_access.setLevel(access_level)


def _parse_level(raw: str | None, *, default: str = "INFO") -> int:
    """把字符串级别解析成 logging 常量;无法解析回退 default。"""
    # Python 3.11+:官方 str → int 映射,替代被 deprecated 的 getLevelName(str)
    mapping = logging.getLevelNamesMapping()
    if raw:
        lvl = mapping.get(raw.upper())
        if lvl is not None:
            return lvl
    return mapping.get(default, logging.INFO)

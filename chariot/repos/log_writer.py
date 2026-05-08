"""请求流水落库封装:把 agent 想记的一条 log 写进 `logs` 表。

架构决策
--------
- 服务层不接受 request-scoped session;`LogWriter` 自己从 `database.session._state`
  取 session_maker 按需开 session
- 失败兜底:写 log 出任何异常都不向上冒(主请求已经完成),只 `logger.warning`
- 不做采样 / 批量,单机场景下 SQLite INSERT 成本可接受(~ms 级)

用法
----
`log_writer.record(...)`。本模块暴露单例 `log_writer`,和 `Agent` / `MockModel`
的"类 + 单例"风格一致。
"""

from __future__ import annotations

import logging
from typing import Literal

from chariot.database.session import get_session_maker
from chariot.repos.log_repo import LogRepo

_log = logging.getLogger("chariot.server.log_writer")

LogStatus = Literal["ok", "error", "timeout"]


class LogWriter:
    """logs 表写入器(服务层单例)。失败兜底,不向上冒异常。"""

    async def record(
        self,
        *,
        provider: str | None,
        status: LogStatus,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        latency_ms: int | None = None,
        error: str | None = None,
    ) -> None:
        """写一条 log。DB 未初始化 / 写失败只打 logger,不抛。"""
        session_maker = get_session_maker()
        if session_maker is None:
            _log.debug("log_writer: session_maker 未初始化,跳过落库")
            return
        try:
            async with session_maker() as session:
                await LogRepo(session).create(
                    provider=provider,
                    status=status,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency_ms,
                    error=error,
                )
        except Exception as e:
            _log.warning(
                "log_writer: 写入失败,忽略 (status=%s): %s",
                status,
                e,
            )


log_writer = LogWriter()

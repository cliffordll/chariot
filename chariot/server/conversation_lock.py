"""ConversationLockManager — 跨客户端 advisory lock,串行化同 conversation_id 的并发写。

并发场景:UI 和 CLI(或两个 CLI session)同时打同一 conversation_id 时,server
端 `Agent._stream_tool_loop` 会各自 `load history → append user → call model →
append assistant`;两边都看到对方写之前的 DB 快照,各自写完 DB 序列错乱(详见
0.4.x → 0.5.0 的"问题清单",issue 3)。

设计取舍
--------
- **in-memory `asyncio.Lock` per conv_id**(0.5.0 选这条):chariot 是单进程
  server,async 协程在事件循环里串行,asyncio.Lock 就够用。timeout 走
  `asyncio.wait_for`,超时 → 503 `conversation_busy`
- **DB-level lock(SQLite BEGIN IMMEDIATE)**:多进程部署才需要;0.5.0 不做,
  作为后续选项

封装
----
单例 ClassVar `_locks: dict[conv_id, asyncio.Lock]`,惰性创建。`acquire(conv_id)`
是 async context manager,`yield` 期间持锁,退出自动释放。lock 实例不主动
GC(数量级 = 活跃 conv 数,可忽略)。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import ClassVar

from chariot.server.service.exceptions import ServiceError

_DEFAULT_TIMEOUT_S = 30.0


class ConversationLockManager:
    """每个 conversation_id 一把 in-memory asyncio.Lock,串行化同 conv 的并发写。

    用法::

        async with ConversationLockManager.acquire(conv_id):
            # critical section:load history + persist + call model + ...

    超时(默认 30s,env `CHARIOT_CONV_LOCK_TIMEOUT_S` 覆盖)→ raise
    `ServiceError(503, "conversation_busy")`。
    """

    _locks: ClassVar[dict[str, asyncio.Lock]] = {}

    @classmethod
    @asynccontextmanager
    async def acquire(
        cls,
        conv_id: str,
        *,
        timeout_s: float | None = None,
    ) -> AsyncIterator[None]:
        """获取 conv_id 对应锁;async with 块结束时自动释放。"""
        if timeout_s is None:
            timeout_s = cls._read_timeout_env()

        # 惰性创建 Lock —— 在单线程 asyncio 里 get / set 之间没有 await,
        # 同 conv 的两次 acquire 不会竞争出多把 Lock
        lock = cls._locks.get(conv_id)
        if lock is None:
            lock = asyncio.Lock()
            cls._locks[conv_id] = lock

        try:
            await asyncio.wait_for(lock.acquire(), timeout=timeout_s)
        except TimeoutError as e:
            raise ServiceError(
                status=503,
                code="conversation_busy",
                message=(
                    f"conversation {conv_id} 锁等待超时({timeout_s}s),另一个客户端正在写,稍后重试"
                ),
            ) from e

        try:
            yield
        finally:
            lock.release()

    @staticmethod
    def _read_timeout_env() -> float:
        # pyright stubs 对 os.environ.get 推断不全(reportUnknownMemberType)
        raw: str | None = os.environ.get("CHARIOT_CONV_LOCK_TIMEOUT_S")  # pyright: ignore[reportUnknownMemberType]
        if raw is None:
            return _DEFAULT_TIMEOUT_S
        try:
            v = float(raw)
        except ValueError:
            return _DEFAULT_TIMEOUT_S
        return v if v > 0 else _DEFAULT_TIMEOUT_S

    @classmethod
    def reset_for_tests(cls) -> None:
        """测试 fixture 用:清空所有锁,避免跨测试污染。"""
        cls._locks.clear()

"""ConvoLockManager — 同 convo_id 的并发写串行化。

**双层锁(0.6.0+)**:

- **进程内**:`asyncio.Lock` per convo_id —— 同进程内同 convo 串行
- **跨进程**:SQLite `BEGIN IMMEDIATE` 短事务
  (`ConvoRepo.with_advisory_lock`) —— 多进程同 convo 串行写

旧 0.5.0 只有进程内锁(单 server 进程模式);0.6.0 库化后多 surface 进程
(CLI + Gateway + sidecar)可能同时写同 convo,加 SQLite 层保护。

设计取舍
--------
- **进程内 in-memory `asyncio.Lock`**:async 协程在事件循环里串行,asyncio.Lock
  足够。timeout 走 `asyncio.wait_for`,超时 → `ConvoLockTimeout(layer="local")`
  (0.6.0+)/ `ServiceError(503)`(0.5.0 兼容,过渡期)
- **DB-level lock(SQLite BEGIN IMMEDIATE)**:多进程并发触发;0.6.0 新增。
  实现见 `ConvoRepo.with_advisory_lock`(短事务 + busy_timeout 重试)

封装
----
单例 ClassVar `_locks: dict[convo_id, asyncio.Lock]`,惰性创建。`acquire(convo_id)`
是 async context manager,`yield` 期间持锁,退出自动释放。lock 实例不主动
GC(数量级 = 活跃 convo 数,可忽略)。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, ClassVar

from chariot.server.service.exceptions import ServiceError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_DEFAULT_TIMEOUT_S = 30.0


class ConvoLockManager:
    """每 convo_id 一把双层锁(进程内 + 可选 SQLite advisory)。

    用法::

        # 单进程模式(0.5.0 行为,过渡期 server/ 调用)
        async with ConvoLockManager.acquire(convo_id):
            # critical section

        # 双层模式(0.6.0+,AIAgent / AgentLoop 调用)
        async with ConvoLockManager.acquire(convo_id, db_session=session):
            # critical section(进程内锁 + DB advisory 都持有)

    错误形态:
    - 进程内锁超时 → `ServiceError(503, "convo_busy")`(0.5.0 行为,
      过渡期保留;controller 层期望此异常)
    - DB 锁超时 → `ConvoLockTimeout(layer="db")`(0.6.0 新)
    """

    _locks: ClassVar[dict[str, asyncio.Lock]] = {}

    @classmethod
    @asynccontextmanager
    async def acquire(
        cls,
        convo_id: str,
        *,
        db_session: AsyncSession | None = None,
        timeout_s: float | None = None,
    ) -> AsyncGenerator[None]:
        """获取 convo_id 对应锁;async with 块结束时自动释放。

        - `db_session=None`:仅进程内锁(0.5.0 行为)
        - `db_session` 非空:进程内 + SQLite advisory 双层(0.6.0+ 行为)
        """
        if timeout_s is None:
            timeout_s = cls._read_timeout_env()

        # 惰性创建 Lock —— 在单线程 asyncio 里 get / set 之间没有 await,
        # 同 convo 的两次 acquire 不会竞争出多把 Lock
        lock = cls._locks.get(convo_id)
        if lock is None:
            lock = asyncio.Lock()
            cls._locks[convo_id] = lock

        try:
            await asyncio.wait_for(lock.acquire(), timeout=timeout_s)
        except TimeoutError as e:
            raise ServiceError(
                status=503,
                code="convo_busy",
                message=(f"convo {convo_id} 锁等待超时({timeout_s}s),另一个客户端正在写,稍后重试"),
            ) from e

        try:
            if db_session is None:
                yield
            else:
                # 双层:已持进程内锁;再获 DB advisory lock 包 critical section
                # import 放方法内避免循环 import(repos → agent → repos)
                from chariot.repos.convo_repo import ConvoRepo

                async with ConvoRepo(db_session).with_advisory_lock(convo_id, timeout_s=timeout_s):
                    yield
        finally:
            lock.release()

    @staticmethod
    def _read_timeout_env() -> float:
        # pyright stubs 对 os.environ.get 推断不全(reportUnknownMemberType)
        raw: str | None = os.environ.get("CHARIOT_CONVO_LOCK_TIMEOUT_S")  # pyright: ignore[reportUnknownMemberType]
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

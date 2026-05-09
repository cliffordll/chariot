"""ConversationLockManager — 同 conversation_id 的并发写串行化。

**双层锁**:

- **进程内**:`asyncio.Lock` per conversation_id —— 同进程内同 conversation 串行
- **跨进程**:SQLite `BEGIN IMMEDIATE` 短事务
  (`ConversationRepo.with_advisory_lock`) —— 多进程同 conversation 串行写

库化后多 surface 进程(CLI + Gateway + sidecar)可能同时写同 conversation,
靠 SQLite 层保护跨进程互斥。

设计取舍
--------
- **进程内 in-memory `asyncio.Lock`**:async 协程在事件循环里串行,asyncio.Lock
  足够。timeout 走 `asyncio.wait_for`,超时 → `ConversationLockTimeout(layer="local")`
- **DB-level lock(SQLite BEGIN IMMEDIATE)**:多进程并发触发;实现见
  `ConversationRepo.with_advisory_lock`(短事务 + busy_timeout 重试),超时 →
  `ConversationLockTimeout(layer="db")`

封装
----
单例 ClassVar `_locks: dict[conversation_id, asyncio.Lock]`,惰性创建。`acquire(conversation_id)`
是 async context manager,`yield` 期间持锁,退出自动释放。lock 实例不主动
GC(数量级 = 活跃 conversation 数,可忽略)。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, ClassVar

from chariot.agent.exceptions import ConversationLockTimeout

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_DEFAULT_TIMEOUT_S = 30.0


class ConversationLockManager:
    """每 conversation_id 一把双层锁(进程内 + 可选 SQLite advisory)。

    用法::

        # 单层模式(进程内锁,无跨进程保护)
        async with ConversationLockManager.acquire(conversation_id):
            # critical section

        # 双层模式(AIAgent / AgentLoop 调用)
        async with ConversationLockManager.acquire(conversation_id, db_session=session):
            # critical section(进程内锁 + DB advisory 都持有)

    错误形态:
    - 进程内锁超时 → `ConversationLockTimeout(layer="local")`
    - DB 锁超时   → `ConversationLockTimeout(layer="db")`
    """

    _locks: ClassVar[dict[str, asyncio.Lock]] = {}

    @classmethod
    @asynccontextmanager
    async def acquire(
        cls,
        conversation_id: str,
        *,
        db_session: AsyncSession | None = None,
        timeout_s: float | None = None,
    ) -> AsyncGenerator[None]:
        """获取 conversation_id 对应锁;async with 块结束时自动释放。

        - `db_session=None`:仅进程内锁
        - `db_session` 非空:进程内 + SQLite advisory 双层
        """
        if timeout_s is None:
            timeout_s = cls._read_timeout_env()

        # 惰性创建 Lock —— 在单线程 asyncio 里 get / set 之间没有 await,
        # 同 conversation 的两次 acquire 不会竞争出多把 Lock
        lock = cls._locks.get(conversation_id)
        if lock is None:
            lock = asyncio.Lock()
            cls._locks[conversation_id] = lock

        try:
            await asyncio.wait_for(lock.acquire(), timeout=timeout_s)
        except TimeoutError as e:
            raise ConversationLockTimeout(
                f"conversation {conversation_id} 锁等待超时({timeout_s}s),另一个客户端正在写,稍后重试",
                layer="local",
            ) from e

        try:
            if db_session is None:
                yield
            else:
                # 双层:已持进程内锁;再获 DB advisory lock 包 critical section
                # import 放方法内避免循环 import(repos → agent → repos)
                from chariot.repos.conversation_repo import ConversationRepo

                async with ConversationRepo(db_session).with_advisory_lock(conversation_id, timeout_s=timeout_s):
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

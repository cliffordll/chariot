"""ConvoLockManager + ConvoRepo.with_advisory_lock 单测(0.6.0 双层锁)。

覆盖:
- 进程内并发:`asyncio.gather` 起 N 个并发 `acquire(convo_id)`,critical section
  实际串行(用 list.append 验证)
- with_advisory_lock 正常路径:commit 后数据可见
- with_advisory_lock 异常路径:ROLLBACK 后数据不可见
- DB 锁超时:用两个 session,一个持 BEGIN IMMEDIATE 不 commit,另一个调
  `with_advisory_lock` 应在 busy_timeout * max_retries 后抛
  `ConvoLockTimeout(layer="db")`
- 双层 acquire:`acquire(convo_id, db_session=s)` 同时持进程内锁 + DB 锁

注:跨进程并发(`multiprocessing.Process`)在 Windows 下 fixture 复杂,
0.6.0 用"file-based SQLite + 多 session"模拟跨连接行为(SQLite 锁是 OS
级 file lock,跨 connection / 跨进程行为一致)。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.convo_lock import ConvoLockManager
from chariot.agent.exceptions import ConvoLockTimeout
from chariot.database.session import dispose_db, init_db
from chariot.repos.convo_repo import ConvoRepo


@pytest_asyncio.fixture
async def db_path(tmp_path: Path) -> Path:
    """临时 file SQLite,跑 migrations。"""
    p = tmp_path / "chariot.db"
    sm = await init_db(p)
    await dispose_db()
    # 把 sessionmaker 也给到测试,但本 fixture 只返路径
    del sm
    return p


@pytest_asyncio.fixture
async def session_a(db_path: Path) -> AsyncIterator[AsyncSession]:
    """独立 session A(独立 connection)。"""
    sm = await init_db(db_path)
    async with sm() as s:
        yield s
    await dispose_db()


@pytest_asyncio.fixture
async def session_b(db_path: Path) -> AsyncIterator[AsyncSession]:
    """独立 session B(独立 connection,跟 A 共享同 db file)。"""
    sm = await init_db(db_path)
    async with sm() as s:
        yield s
    await dispose_db()


@pytest.fixture(autouse=True)
def reset_lock_manager() -> None:
    """每 test 起前清空进程内锁。"""
    ConvoLockManager.reset_for_tests()


# ---------------------------------------------------------------------------
# 进程内 asyncio.Lock 串行化
# ---------------------------------------------------------------------------


class TestInProcessSerialization:
    """同进程内同 convo_id 的并发 acquire 实际串行。"""

    async def test_concurrent_acquires_serialize(self) -> None:
        """10 个并发 acquire 同 conv,critical section 顺序进出。"""
        order: list[int] = []
        active = 0
        max_active = 0

        async def worker(idx: int) -> None:
            nonlocal active, max_active
            async with ConvoLockManager.acquire("conv_x"):
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0.01)
                order.append(idx)
                active -= 1

        await asyncio.gather(*(worker(i) for i in range(10)))

        # critical section 任何时刻只有 1 个 worker
        assert max_active == 1
        # 所有 worker 都跑过(顺序不强制,但 10 个都进出)
        assert sorted(order) == list(range(10))

    async def test_different_convo_ids_parallel(self) -> None:
        """不同 convo_id 各自有锁,可并行。"""
        active = 0
        max_active = 0

        async def worker(convo_id: str) -> None:
            nonlocal active, max_active
            async with ConvoLockManager.acquire(convo_id):
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0.05)
                active -= 1

        await asyncio.gather(*(worker(f"conv_{i}") for i in range(5)))
        # 5 个不同 conv 完全并行
        assert max_active == 5


# ---------------------------------------------------------------------------
# ConvoRepo.with_advisory_lock 基础行为
# ---------------------------------------------------------------------------


class TestAdvisoryLockBasic:
    """with_advisory_lock 自身行为(探测 + 释放,不包整段事务)。"""

    async def test_acquires_and_yields(self, session_a: AsyncSession) -> None:
        """探测成功后 yield;critical section 内 repo.create 自己 commit,数据可见。"""
        repo = ConvoRepo(session_a)
        async with repo.with_advisory_lock("conv_a"):
            await repo.create("conv_a", title="hello")
        # critical section 内 commit 后数据可见(with_advisory_lock 不接管事务)
        conv = await repo.get("conv_a")
        assert conv is not None
        assert conv.title == "hello"

    async def test_exception_in_critical_section_propagates(self, session_a: AsyncSession) -> None:
        """critical section 内异常正常上抛(with_advisory_lock 不接管 ROLLBACK)。"""
        repo = ConvoRepo(session_a)

        class _BoomError(Exception):
            pass

        with pytest.raises(_BoomError):
            async with repo.with_advisory_lock("conv_b"):
                raise _BoomError("simulated failure inside critical section")


# ---------------------------------------------------------------------------
# 跨 connection(模拟跨进程)串行 + 超时
# ---------------------------------------------------------------------------


class TestAdvisoryLockCrossConnection:
    """两个独立 session(各自 connection,共享 file)模拟跨进程并发。"""

    async def test_second_acquire_times_out_when_first_holds(
        self,
        session_a: AsyncSession,
        session_b: AsyncSession,
    ) -> None:
        """A 持 BEGIN IMMEDIATE 不 commit;B 调 with_advisory_lock(短 timeout)
        应在重试耗尽后抛 ConvoLockTimeout(layer="db")。
        """
        # A 手动开 IMMEDIATE 事务,占住 RESERVED 锁
        await session_a.execute(text("PRAGMA busy_timeout = 100"))
        await session_a.execute(text("BEGIN IMMEDIATE"))
        try:
            repo_b = ConvoRepo(session_b)
            with pytest.raises(ConvoLockTimeout) as exc_info:
                async with repo_b.with_advisory_lock(
                    "conv_x",
                    timeout_s=0.1,
                    max_retries=2,
                ):
                    pass
            assert exc_info.value.layer == "db"
        finally:
            await session_a.execute(text("ROLLBACK"))


# ---------------------------------------------------------------------------
# 双层 acquire(进程内锁 + DB advisory)
# ---------------------------------------------------------------------------


class TestDoubleLayerAcquire:
    """`ConvoLockManager.acquire(convo_id, db_session=s)` 同时持两层锁。"""

    async def test_acquire_with_db_session_writes_and_commits(
        self, session_a: AsyncSession
    ) -> None:
        """带 db_session 的 acquire,critical section 内写数据,正常 commit。"""
        async with ConvoLockManager.acquire("conv_dl", db_session=session_a):
            repo = ConvoRepo(session_a)
            await repo.create("conv_dl", title="double-layer")
        # 退出后,数据可见
        conv = await ConvoRepo(session_a).get("conv_dl")
        assert conv is not None
        assert conv.title == "double-layer"

    async def test_acquire_without_db_session_local_only(self, session_a: AsyncSession) -> None:
        """db_session=None 不触发 DB 锁(只进程内);critical section 内自己
        commit 也能写数据(没有自动 BEGIN IMMEDIATE 包裹)。
        """
        async with ConvoLockManager.acquire("conv_local"):
            repo = ConvoRepo(session_a)
            await repo.create("conv_local", title="local-only")
        conv = await ConvoRepo(session_a).get("conv_local")
        assert conv is not None


# ---------------------------------------------------------------------------
# ConvoLockTimeout 字段
# ---------------------------------------------------------------------------


class TestExceptionFields:
    """ConvoLockTimeout 携带 layer 字段,surface 层据此映射 error_type。"""

    async def test_db_timeout_layer_field(
        self,
        session_a: AsyncSession,
        session_b: AsyncSession,
    ) -> None:
        await session_a.execute(text("PRAGMA busy_timeout = 100"))
        await session_a.execute(text("BEGIN IMMEDIATE"))
        try:
            repo_b = ConvoRepo(session_b)
            with pytest.raises(ConvoLockTimeout) as exc_info:
                async with repo_b.with_advisory_lock("conv_y", timeout_s=0.1, max_retries=2):
                    pass
            assert exc_info.value.layer == "db"
            assert "DB advisory lock" in str(exc_info.value)
        finally:
            await session_a.execute(text("ROLLBACK"))

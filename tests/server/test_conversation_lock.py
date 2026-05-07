"""ConversationLockManager 测试(0.5.0 S.3)。

覆盖:
- 同 conv:第一个 acquire 占住,第二个 acquire 等
- 不同 conv:互不阻塞
- 超时 → 503 conversation_busy
- 锁释放后第二个能正常 acquire
"""

from __future__ import annotations

import asyncio

import pytest

from chariot.agent.conversation_lock import ConversationLockManager
from chariot.server.service.exceptions import ServiceError


@pytest.fixture(autouse=True)
def _clean_locks() -> None:
    """每个 case 起步清空 _locks,避免跨测试污染。"""
    ConversationLockManager.reset_for_tests()


CONV_A = "01ABCDEF0000000000000000A1"
CONV_B = "01ABCDEF0000000000000000B2"


# ============================================================
# 同 conv:串行
# ============================================================


async def test_same_conv_second_blocks_until_first_release() -> None:
    """同 conv 第一个 acquire 持锁时,第二个被阻塞。"""
    order: list[str] = []

    async def first() -> None:
        async with ConversationLockManager.acquire(CONV_A, timeout_s=5):
            order.append("first_in")
            await asyncio.sleep(0.05)
            order.append("first_out")

    async def second() -> None:
        # 等 first 起步抓锁
        await asyncio.sleep(0.01)
        async with ConversationLockManager.acquire(CONV_A, timeout_s=5):
            order.append("second_in")

    await asyncio.gather(first(), second())
    assert order == ["first_in", "first_out", "second_in"]


async def test_lock_released_after_exit_allows_next() -> None:
    """async with 块退出 → 立即释放,后续 acquire 顺利。"""
    async with ConversationLockManager.acquire(CONV_A, timeout_s=1):
        pass
    # 退出后再 acquire 不应阻塞
    async with ConversationLockManager.acquire(CONV_A, timeout_s=1):
        pass


async def test_lock_released_on_exception() -> None:
    """async with 块抛异常 → 锁仍释放(finally 路径)。"""

    class _BoomError(Exception):
        pass

    with pytest.raises(_BoomError):
        async with ConversationLockManager.acquire(CONV_A, timeout_s=1):
            raise _BoomError

    # 异常退出后再 acquire 不应阻塞
    async with ConversationLockManager.acquire(CONV_A, timeout_s=1):
        pass


# ============================================================
# 不同 conv:并行
# ============================================================


async def test_different_convs_do_not_block_each_other() -> None:
    """conv A 和 conv B 各自有锁,互不阻塞 → 并行完成。"""
    order: list[str] = []

    async def hold_a() -> None:
        async with ConversationLockManager.acquire(CONV_A, timeout_s=5):
            order.append("a_in")
            await asyncio.sleep(0.05)
            order.append("a_out")

    async def hold_b() -> None:
        async with ConversationLockManager.acquire(CONV_B, timeout_s=5):
            order.append("b_in")
            await asyncio.sleep(0.05)
            order.append("b_out")

    await asyncio.gather(hold_a(), hold_b())
    # 不要求严格顺序,但两边的 in 都在两边的 out 之前(说明并行)
    assert order.index("a_in") < order.index("b_out")
    assert order.index("b_in") < order.index("a_out")


# ============================================================
# 超时
# ============================================================


async def test_timeout_raises_service_error_503() -> None:
    """同 conv 第一把锁迟迟不放,第二个超时 → ServiceError(503, conversation_busy)。"""
    holder_done = asyncio.Event()

    async def holder() -> None:
        async with ConversationLockManager.acquire(CONV_A, timeout_s=5):
            await holder_done.wait()  # 一直占着

    async def waiter() -> None:
        # 等 holder 抓住
        await asyncio.sleep(0.01)
        with pytest.raises(ServiceError) as exc:
            async with ConversationLockManager.acquire(CONV_A, timeout_s=0.05):
                pass  # pragma: no cover · 不应进入
        assert exc.value.status == 503
        assert exc.value.code == "conversation_busy"
        # 让 holder 收尾
        holder_done.set()

    await asyncio.gather(holder(), waiter())


# ============================================================
# env 配置
# ============================================================


def test_env_default_timeout_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """env 没设 → 30.0 默认。"""
    monkeypatch.delenv("CHARIOT_CONV_LOCK_TIMEOUT_S", raising=False)
    assert ConversationLockManager._read_timeout_env() == 30.0


def test_env_invalid_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """非数字 / 非正 → fallback。"""
    monkeypatch.setenv("CHARIOT_CONV_LOCK_TIMEOUT_S", "not-a-number")
    assert ConversationLockManager._read_timeout_env() == 30.0
    monkeypatch.setenv("CHARIOT_CONV_LOCK_TIMEOUT_S", "0")
    assert ConversationLockManager._read_timeout_env() == 30.0
    monkeypatch.setenv("CHARIOT_CONV_LOCK_TIMEOUT_S", "-5")
    assert ConversationLockManager._read_timeout_env() == 30.0


def test_env_valid_value_taken(monkeypatch: pytest.MonkeyPatch) -> None:
    """有效正数生效。"""
    monkeypatch.setenv("CHARIOT_CONV_LOCK_TIMEOUT_S", "10.5")
    assert ConversationLockManager._read_timeout_env() == 10.5

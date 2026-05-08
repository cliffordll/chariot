"""AgentRegistry 单测(0.6.5)。

覆盖:
- acquire 同 session_key → 同实例(cache hit)
- acquire 不同 session_key → 不同实例
- acquire 带 provider_overrides → 进 entry.options 重建 Provider
- provider_overrides 仅首次 acquire 时生效(cached agent 忽略后续 overrides)
- LRU evict 超容量
- release 单独清某 session
- aclose_all 清全部
- 并发 acquire 同 session_key → 不重复建
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from chariot.agent.registry import AgentRegistry
from chariot.database.session import dispose_db


@pytest_asyncio.fixture(autouse=True)
async def _isolate_registry() -> AsyncIterator[None]:
    """每 test 前后清空 registry + DB engine,保证 test 间无串台。"""
    await AgentRegistry.aclose_all()
    await dispose_db()
    yield
    await AgentRegistry.aclose_all()
    await dispose_db()


@pytest_asyncio.fixture
async def db_path(tmp_path: Path) -> Path:
    """单独 DB 文件;由 init_db 自动跑 migrations + seed。"""
    return tmp_path / "chariot.db"


# ---------------------------------------------------------------------------
# acquire 命中 / 不同 key
# ---------------------------------------------------------------------------


class TestAcquireHit:
    async def test_same_session_key_returns_same_instance(self, db_path: Path) -> None:
        a = await AgentRegistry.acquire("session-A", db_path=db_path)
        b = await AgentRegistry.acquire("session-A", db_path=db_path)
        assert a is b
        assert AgentRegistry.size() == 1

    async def test_different_session_keys_separate_instances(self, db_path: Path) -> None:
        a = await AgentRegistry.acquire("session-A", db_path=db_path)
        b = await AgentRegistry.acquire("session-B", db_path=db_path)
        assert a is not b
        assert AgentRegistry.size() == 2


# ---------------------------------------------------------------------------
# provider_overrides 在首次 acquire 时生效
# ---------------------------------------------------------------------------


class TestProviderOverrides:
    async def test_overrides_apply_on_first_acquire(self, db_path: Path) -> None:
        """provider_overrides 进 entry.options;首个 acquire 用合并后的 options 建 Provider。

        注:fresh DB 由 init_db 自动 seed mock entry,把 mock 当 target;mock
        provider 不消费 options,所以这里仅验证"调用链通"+"acquire 不抛"。
        覆盖 Provider 实例 config 反映 override 的更深 case 在
        `tests/agent/test_run.py::TestFromDbProviderOverrides` 里。
        """
        agent = await AgentRegistry.acquire(
            "session-A",
            db_path=db_path,
            provider_overrides={"mock": {"foo": "bar"}},
        )
        assert "mock" in agent.providers

    async def test_subsequent_acquire_ignores_overrides(self, db_path: Path) -> None:
        """同 session_key 第二次 acquire → cache 命中,即使传新 overrides 也忽略。"""
        a = await AgentRegistry.acquire("session-A", db_path=db_path)
        b = await AgentRegistry.acquire(
            "session-A",
            db_path=db_path,
            provider_overrides={"mock": {"x": "y"}},  # ignored
        )
        assert a is b


# ---------------------------------------------------------------------------
# LRU 淘汰
# ---------------------------------------------------------------------------


class TestLruEviction:
    """超 _MAX_AGENTS 时弹最老的(不 close,GC 处理)。"""

    @pytest.fixture(autouse=True)
    def _shrink_max(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(AgentRegistry, "_MAX_AGENTS", 3)

    async def test_evict_lru_when_over_capacity(self, db_path: Path) -> None:
        a = await AgentRegistry.acquire("s1", db_path=db_path)
        b = await AgentRegistry.acquire("s2", db_path=db_path)
        await AgentRegistry.acquire("s3", db_path=db_path)
        assert AgentRegistry.size() == 3

        await AgentRegistry.acquire("s4", db_path=db_path)
        assert AgentRegistry.size() == 3
        # s1 被弹;再 acquire 同 key → 重新装载,新实例
        a_again = await AgentRegistry.acquire("s1", db_path=db_path)
        assert a_again is not a
        # 加 a_again 又触发 evict 一个最老的;此时 cache 应有 s3 / s4 / s1(a_again)
        # s2 又被弹了 → 重新 acquire 也是新实例
        b_again = await AgentRegistry.acquire("s2", db_path=db_path)
        assert b_again is not b


# ---------------------------------------------------------------------------
# release / aclose_all
# ---------------------------------------------------------------------------


class TestReleaseAndAclose:
    async def test_release_removes_specific_session(self, db_path: Path) -> None:
        a = await AgentRegistry.acquire("s1", db_path=db_path)
        await AgentRegistry.acquire("s2", db_path=db_path)
        assert AgentRegistry.size() == 2

        await AgentRegistry.release("s1")
        assert AgentRegistry.size() == 1
        # 重新 acquire s1 → 新实例
        a_again = await AgentRegistry.acquire("s1", db_path=db_path)
        assert a_again is not a

    async def test_release_unknown_key_no_op(self, db_path: Path) -> None:
        """缺失 key 不抛(幂等)。"""
        await AgentRegistry.release("ghost")
        await AgentRegistry.release("ghost")
        assert AgentRegistry.size() == 0

    async def test_aclose_all_clears_everything(self, db_path: Path) -> None:
        await AgentRegistry.acquire("s1", db_path=db_path)
        await AgentRegistry.acquire("s2", db_path=db_path)
        assert AgentRegistry.size() == 2

        await AgentRegistry.aclose_all()
        assert AgentRegistry.size() == 0

    async def test_aclose_all_idempotent(self) -> None:
        await AgentRegistry.aclose_all()
        await AgentRegistry.aclose_all()  # 二次 ok
        assert AgentRegistry.size() == 0


# ---------------------------------------------------------------------------
# 并发 acquire 同 session_key 不重复建
# ---------------------------------------------------------------------------


class TestConcurrent:
    async def test_concurrent_same_session_key_no_double_build(self, db_path: Path) -> None:
        """N coroutine 并发 acquire(same key)→ 全部拿到同一实例。"""
        results = await asyncio.gather(
            *(AgentRegistry.acquire("shared", db_path=db_path) for _ in range(15))
        )
        assert all(r is results[0] for r in results)
        assert AgentRegistry.size() == 1

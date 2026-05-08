"""ClientCache / ClientSpec 单测(0.6.5)。

覆盖:
- spec frozen + hashable + build() 出 httpx.AsyncClient
- ClientCache.get 命中:同 spec → 同实例
- ClientCache.get 未命中:不同 spec → 不同实例
- LRU evict:超 _MAX_VARIANTS 时弹最老的并 aclose
- 并发 race:N coroutine 同 spec → 不重复建
- aclose_all 关全部 + 清空,幂等
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import ClassVar

import httpx
import pytest
import pytest_asyncio

from chariot.providers.clients import ClientCache, ClientSpec


def _spec(*, base_url: str = "https://api.test", api_key: str = "k1") -> ClientSpec:
    return ClientSpec(
        provider_type="anthropic",
        base_url=base_url,
        api_key=api_key,
        headers=(
            ("x-api-key", api_key),
            ("anthropic-version", "2023-06-01"),
            ("content-type", "application/json"),
        ),
    )


@pytest_asyncio.fixture(autouse=True)
async def _isolate_cache() -> AsyncIterator[None]:
    """每 test 前后清空 cache,保证 test 之间无串台。"""
    await ClientCache.aclose_all()
    yield
    await ClientCache.aclose_all()


# ---------------------------------------------------------------------------
# ClientSpec
# ---------------------------------------------------------------------------


class TestClientSpec:
    def test_frozen_hashable(self) -> None:
        s1 = _spec()
        s2 = _spec()
        assert s1 == s2
        assert hash(s1) == hash(s2)
        assert {s1, s2} == {s1}  # set dedup 验 hashable

    def test_different_base_url_neq(self) -> None:
        assert _spec(base_url="https://a") != _spec(base_url="https://b")

    def test_different_api_key_neq(self) -> None:
        assert _spec(api_key="k1") != _spec(api_key="k2")

    def test_build_returns_async_client(self) -> None:
        client = _spec().build()
        assert isinstance(client, httpx.AsyncClient)

    def test_build_carries_headers(self) -> None:
        client = _spec(api_key="my-key").build()
        assert client.headers["x-api-key"] == "my-key"
        assert client.headers["anthropic-version"] == "2023-06-01"


# ---------------------------------------------------------------------------
# ClientCache.get
# ---------------------------------------------------------------------------


class TestClientCacheGet:
    async def test_same_spec_returns_same_instance(self) -> None:
        a = await ClientCache.get(_spec())
        b = await ClientCache.get(_spec())
        assert a is b
        assert ClientCache.size() == 1

    async def test_different_spec_returns_different_instance(self) -> None:
        a = await ClientCache.get(_spec(base_url="https://a"))
        b = await ClientCache.get(_spec(base_url="https://b"))
        assert a is not b
        assert ClientCache.size() == 2

    async def test_different_api_key_separate_clients(self) -> None:
        a = await ClientCache.get(_spec(api_key="k1"))
        b = await ClientCache.get(_spec(api_key="k2"))
        assert a is not b
        assert ClientCache.size() == 2


# ---------------------------------------------------------------------------
# LRU eviction
# ---------------------------------------------------------------------------


class TestLruEviction:
    """超 _MAX_VARIANTS 时弹最老的;evicted client 被 aclose。"""

    _ORIG_MAX: ClassVar[int] = ClientCache._MAX_VARIANTS

    @pytest.fixture(autouse=True)
    def _shrink_max(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """缩小 cache 上限到 3,方便快速触发 evict。"""
        monkeypatch.setattr(ClientCache, "_MAX_VARIANTS", 3)

    async def test_evict_lru_when_over_capacity(self) -> None:
        # 填到上限
        c1 = await ClientCache.get(_spec(base_url="https://1"))
        c2 = await ClientCache.get(_spec(base_url="https://2"))
        c3 = await ClientCache.get(_spec(base_url="https://3"))
        assert ClientCache.size() == 3

        # 加第 4 个 → 弹 c1
        c4 = await ClientCache.get(_spec(base_url="https://4"))
        assert ClientCache.size() == 3
        assert c1.is_closed is True
        # c2 / c3 / c4 还在
        assert c2.is_closed is False
        assert c3.is_closed is False
        assert c4.is_closed is False

    async def test_get_refreshes_lru_position(self) -> None:
        """LRU:重新 get 已存在的 spec → 它变成最新,不会被下次 evict 弹。"""
        c1 = await ClientCache.get(_spec(base_url="https://1"))
        c2 = await ClientCache.get(_spec(base_url="https://2"))
        c3 = await ClientCache.get(_spec(base_url="https://3"))

        # 重新拿 c1 → c1 变最新
        again = await ClientCache.get(_spec(base_url="https://1"))
        assert again is c1

        # 加新的 → 这次该弹 c2(最老的)
        c4 = await ClientCache.get(_spec(base_url="https://4"))
        assert ClientCache.size() == 3
        assert c2.is_closed is True
        assert c1.is_closed is False
        assert c3.is_closed is False
        assert c4.is_closed is False


# ---------------------------------------------------------------------------
# 并发
# ---------------------------------------------------------------------------


class TestConcurrent:
    async def test_concurrent_same_spec_no_duplicate_build(self) -> None:
        """N coroutine 并发 get(同 spec)→ 只构造一次 client(asyncio.Lock 串行)。"""
        import asyncio

        results = await asyncio.gather(*(ClientCache.get(_spec()) for _ in range(20)))
        assert all(r is results[0] for r in results)
        assert ClientCache.size() == 1

    async def test_concurrent_different_specs_isolated(self) -> None:
        """N coroutine 并发不同 spec → 各自建,无 race / 无丢失。"""
        import asyncio

        specs = [_spec(base_url=f"https://h{i}") for i in range(5)]
        clients = await asyncio.gather(*(ClientCache.get(s) for s in specs))
        # 每个 client 都是不同实例
        assert len({id(c) for c in clients}) == 5
        assert ClientCache.size() == 5


# ---------------------------------------------------------------------------
# aclose_all
# ---------------------------------------------------------------------------


class TestAcloseAll:
    async def test_closes_all_clients_and_clears_cache(self) -> None:
        c1 = await ClientCache.get(_spec(base_url="https://1"))
        c2 = await ClientCache.get(_spec(base_url="https://2"))
        assert ClientCache.size() == 2

        await ClientCache.aclose_all()
        assert ClientCache.size() == 0
        assert c1.is_closed is True
        assert c2.is_closed is True

    async def test_idempotent_on_empty_cache(self) -> None:
        """空 cache 调 aclose_all 不抛错(幂等)。"""
        await ClientCache.aclose_all()
        await ClientCache.aclose_all()  # 二次也 ok
        assert ClientCache.size() == 0

"""进程级 httpx.AsyncClient 缓存(0.6.5 起,Hermes 哲学)。

`BaseProvider` 实例从 0.6.5 开始**不再持有** httpx client。client 的生命周期 /
连接池 / 复用 / 并发治理统一收到这里,所有 Provider 共享一个进程级 cache。

设计参考:Hermes `agent/auxiliary_client.py:_client_cache`(模块级 dict + threading.Lock
+ FIFO 64 + event-loop 校验)。chariot 用 asyncio.Lock(纯 async 栈)+ LRU 16。

为什么不绑在 Provider 实例里(对照 0.6.0 旧实现):
- per-call override(`--base-url` / `--api-key` 不同)会重建 Provider → 新 client
  → 连接池空 → 每次 TLS 握手,长跑场景(sidecar / Gateway)累积成可见 latency
- 多个 Provider 实例对应同 (base_url, api_key) 时 client 不共享,浪费连接

抽离后:
- (base_url, api_key, ...) 一致 → 命中同一个 client → keepalive 保住
- Provider 无状态 / 可随便重建 / 不持 lifecycle
- 撤 0.6.0 那套 `BaseProvider.aclose` ABC(资源不归 Provider 管)

并发契约:
- `ClientCache.get(spec)` 多 coroutine 并发安全:全局 asyncio.Lock 包查 / 建 / cache
  写入。临界区只是 dict op + 一次构造,极短;命中 cache 是无锁快路径不成立 ——
  asyncio.Lock 保护 OrderedDict 的 move_to_end + LRU 维护
- 单 client 并发安全靠 httpx 自身保证(它带连接池,N coroutine 共用一个
  `AsyncClient.send` 安全)

Lifecycle:
- 模块级单例(类承载 ClassVar);进程退出时 surface 调 `aclose_all()` 清干净
- LRU evict 时立即 `await client.aclose()`(显式释放连接池;不靠 GC)
- 仍可能有 in-flight coroutine 持引用 evicted client 的情况 —— 短期 in-flight
  会跟 aclose 竞争。0.6.5 阶段先按"evict 时 aclose"实现,如果实测出问题再改
  ref-count 防护
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from typing import ClassVar

import httpx


@dataclass(frozen=True)
class ClientSpec:
    """构造 httpx.AsyncClient 所需的全部参数;frozen + hashable 直接当 cache key。

    `headers` 用 `tuple[tuple[str, str], ...]` 而非 dict 以便 hash;`build()` 时再
    转回 dict 给 httpx。

    cache key 语义:同 spec(==)→ 同 client。任何字段不同(包括 timeout / limits)
    都视为不同 client。理论上更宽松的 key(只 base_url + api_key)可以让 timeout
    调整不重建,但实操几乎不会动 timeout,严格相等更可预测。
    """

    provider_type: str  # "anthropic" / "openai" / "mock"(mock 不该来这里)/ ...
    base_url: str
    api_key: str
    headers: tuple[tuple[str, str], ...]  # 完整请求头(provider 决定)
    max_connections: int = 20
    max_keepalive: int = 10
    connect_timeout_sec: float = 10.0
    read_timeout_sec: float = 300.0
    write_timeout_sec: float = 30.0
    pool_timeout_sec: float = 10.0

    def build(self) -> httpx.AsyncClient:
        """按 spec 构造一个新的 httpx.AsyncClient。"""
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers=dict(self.headers),
            timeout=httpx.Timeout(
                connect=self.connect_timeout_sec,
                read=self.read_timeout_sec,
                write=self.write_timeout_sec,
                pool=self.pool_timeout_sec,
            ),
            limits=httpx.Limits(
                max_connections=self.max_connections,
                max_keepalive_connections=self.max_keepalive,
            ),
        )


class ClientCache:
    """进程级 httpx.AsyncClient 缓存(LRU)。

    使用方式::

        from chariot.providers.clients import ClientCache, ClientSpec

        spec = ClientSpec(provider_type="anthropic", base_url="...", api_key="...",
                          headers=(("x-api-key", "..."), ...))
        client = await ClientCache.get(spec)
        # 用 client 发请求 ...

        # 进程退出:
        await ClientCache.aclose_all()

    模块级单例(ClassVar 字典 + 锁挂在类上;**无**模块级可变变量,符合 CLAUDE.md ⭐)。
    """

    _MAX_VARIANTS: ClassVar[int] = 16

    _cache: ClassVar[OrderedDict[ClientSpec, httpx.AsyncClient]] = OrderedDict()
    _lock: ClassVar[asyncio.Lock] = asyncio.Lock()

    @classmethod
    async def get(cls, spec: ClientSpec) -> httpx.AsyncClient:
        """命中返已有 client;未命中构造 + 缓存 + 必要时 evict 老的。

        并发安全:全程 asyncio.Lock。N 个 coroutine 同 spec 并发 → 第一个建,其余
        等锁 → 看到已建好直接返,**不重复建**(无 leak)。
        """
        async with cls._lock:
            existing = cls._cache.get(spec)
            if existing is not None:
                cls._cache.move_to_end(spec)  # LRU 更新
                return existing

            client = spec.build()
            cls._cache[spec] = client

            # 超容量:弹最老的,显式 aclose
            while len(cls._cache) > cls._MAX_VARIANTS:
                _, evicted = cls._cache.popitem(last=False)
                # 仍可能有 in-flight coroutine 持引用,aclose 会等到那个 coroutine
                # 完成请求才真正关连接池(httpx 行为)
                await evicted.aclose()

            return client

    @classmethod
    async def aclose_all(cls) -> None:
        """关掉所有缓存的 client + 清空 cache(进程退出时调)。

        幂等:重复调 / 空 cache 时也 ok。
        """
        async with cls._lock:
            for client in cls._cache.values():
                await client.aclose()
            cls._cache.clear()

    @classmethod
    def size(cls) -> int:
        """当前 cache 中 client 数量(测试 / debug 用,不加锁,松一致)。"""
        return len(cls._cache)

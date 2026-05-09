"""AgentRegistry — per-session AIAgent 缓存(0.6.5 起)。

替代 0.6.0 的 `AIAgent._current` 单例。背景:

- CLI 进程一次性,单 session,单例 OK;但 sidecar / Gateway 长跑 + 并发多
  session,单例不够用
- Hermes `gateway/run.py:1091` 用 `_agent_cache: OrderedDict[session_key, ...]`
  + 锁 + LRU 治理
- chariot 0.6.5 对齐:`AgentRegistry` 同样 LRU + asyncio.Lock + 跨 surface 复用

Surface 各自决定 session_key 语义:
- CLI:`"process"`(单一 session,整个进程一份 AIAgent)
- sidecar(S.8):JSON-RPC 拿到的 session_id
- Gateway(0.8.0):`f"{user_id}:{convo_id}"` 之类

并发安全:
- 全局 asyncio.Lock 包查 / 建 / 缓存写。临界区只有 dict op + 一次
  `AIAgent.bootstrap`(后者主要 IO 是 DB 装载,不是连接池)
- 同 session_key 的并发 reserve → 第一个建,其余等锁 → 看到已建好直接返
- 不同 session_key 并发 → 串行 build(每个 build 内部 IO 是 sync DB 装载,
  跑得快;实测必要时再细化锁粒度)

LRU:
- `_MAX_AGENTS` 默认 32(Gateway 高并发用户数典型值);超容量 evict 最老的
- evict 时**不**对 AIAgent 做 cleanup(它没 expensive resources;httpx clients
  归 `ClientCache` 管;DB engine 归 `DBState` 管)。GC 自然回收

模块级零自由函数(CLAUDE.md ⭐),所有逻辑收进 `AgentRegistry` 类。
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from chariot.agent.run import AIAgent


class AgentRegistry:
    """per-session AIAgent 缓存。LRU + asyncio.Lock。

    使用方式::

        # surface 启动一段会话:
        agent = await AgentRegistry.reserve(
            session_key="my_session_id",
            db_path=Path("~/.chariot/chariot.db"),
            provider_overrides={"claude": {"base_url": "...", "api_key": "..."}},
        )
        async for ev in agent.run_chat(req):
            ...

        # session 结束:
        await AgentRegistry.release("my_session_id")

        # 进程退出:
        await AgentRegistry.clear()
    """

    _MAX_AGENTS: ClassVar[int] = 32

    _agents: ClassVar[OrderedDict[str, AIAgent]] = OrderedDict()
    _lock: ClassVar[asyncio.Lock] = asyncio.Lock()

    @classmethod
    async def reserve(
        cls,
        session_key: str,
        *,
        db_path: Path,
        provider_overrides: dict[str, dict[str, str]] | None = None,
    ) -> AIAgent:
        """命中 cache 返已有 agent;未命中调 `AIAgent.bootstrap` 装载并缓存。

        `provider_overrides`:形如 `{"claude": {"base_url": X, "api_key": Y}}`,
        仅在**首次** reserve 同 session_key 时生效(命中已有 agent 时忽略后续
        overrides)。语义:overrides 是 session-bound 的,session 创建时定,
        per-call 不再变;要换 overrides 用新的 session_key。

        失败处理:`AIAgent.bootstrap` 抛(DB 装载错 / Provider 配置错)→ 透传
        给 caller;cache 不写入。
        """
        # 惰性 import 避循环:registry → run.py → registry
        from chariot.agent.run import AIAgent

        async with cls._lock:
            existing = cls._agents.get(session_key)
            if existing is not None:
                cls._agents.move_to_end(session_key)
                return existing

            agent = await AIAgent.bootstrap(db_path, provider_overrides=provider_overrides)
            cls._agents[session_key] = agent

            # LRU evict:超上限 → 弹最老的(不需 cleanup,GC 处理)
            while len(cls._agents) > cls._MAX_AGENTS:
                cls._agents.popitem(last=False)

            return agent

    @classmethod
    async def release(cls, session_key: str) -> None:
        """显式释放某 session 的 agent(session 结束时调)。

        缺失 key 不抛;再调也 ok(幂等)。
        """
        async with cls._lock:
            cls._agents.pop(session_key, None)

    @classmethod
    async def clear(cls) -> None:
        """清空 cache 字典(进程退出时调)。

        注意:**不**做资源 cleanup —— AIAgent 自身没昂贵资源(httpx client 归
        `ClientCache`,DB engine 归 `DBState`)。这里只是删 dict entry 让 GC
        回收。`ClientCache` / `DBState` 由 surface 自己关。

        典型 surface 退出顺序:`AgentRegistry.clear()` → `ClientCache.aclose_all()`
        → `dispose_db()`。
        """
        async with cls._lock:
            cls._agents.clear()

    @classmethod
    async def release_prefix(cls, prefix: str) -> None:
        """Release every cached agent whose session key starts with ``prefix``."""

        async with cls._lock:
            for key in [key for key in cls._agents if key.startswith(prefix)]:
                cls._agents.pop(key, None)

    @classmethod
    def size(cls) -> int:
        """当前 cache 中 agent 数量(测试 / debug 用,不加锁,松一致)。"""
        return len(cls._agents)

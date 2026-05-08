"""CLI 运行时 helper:进程级 AIAgent 装载 / 释放(0.6.5 起走 AgentRegistry)。

每个 `chariot <cmd>` 调用都是独立 OS 进程,生命周期里需要:
1. 读 `~/.chariot/chariot.db`(跑 migrations + seed 兜底)
2. 通过 `AgentRegistry.acquire(session_key="process", ...)` 拿 AIAgent
3. 命令执行
4. 释放:`AgentRegistry.aclose_all()` + `ClientCache.aclose_all()` + `dispose_db()`

`installed_runtime()` 把这套打成 `async with` 块。子命令 typer wrapper 用法::

    asyncio.run(_run(...))

    async def _run(...) -> None:
        async with installed_runtime() as agent:
            ...  # 命令业务

    # 或带 per-call override:
    async def _run(provider, model, base_url, api_key, ...) -> None:
        overrides = _build_overrides(provider, base_url, api_key)
        async with installed_runtime(provider_overrides=overrides) as agent:
            ...

0.6.5 改动 vs 0.6.0:
- 撤 `AIAgent.uninstall()`(单例已撤);改 `AgentRegistry.aclose_all()`
- 加 `provider_overrides` 参数,传给 AgentRegistry → 一次性 merge 进 entry.options
- 退出顺序:AgentRegistry → ClientCache → DB(从上层释放到底层)

不在 module 顶层放单例 —— 跟 CLAUDE.md ⭐"模块级零自由函数 + 零可变变量"
保持一致;`installed_runtime` 是 async context manager,内部所有状态挂在
AgentRegistry / ClientCache / DBState 这些类的 ClassVar 上。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from chariot.agent.registry import AgentRegistry
from chariot.agent.run import AIAgent
from chariot.database.session import DEFAULT_DB_PATH, dispose_db
from chariot.providers.clients import ClientCache

# CLI 是单一 session(整个进程),固定 session_key
_CLI_SESSION_KEY = "process"


@asynccontextmanager
async def installed_runtime(
    *,
    provider_overrides: dict[str, dict[str, str]] | None = None,
) -> AsyncIterator[AIAgent]:
    """进程级 AIAgent + DB 连接池 + httpx client 缓存的生命周期。

    `provider_overrides`(0.6.5 起):per-process 注入到 entry.options 的 patch,
    给 CLI `--base-url` / `--api-key` 用;传给 `AgentRegistry.acquire`。

    退出时即使有异常也按 AgentRegistry → ClientCache → DB 顺序释放(防 fixture
    串味 / 连接泄露 / engine 残留)。
    """
    agent = await AgentRegistry.acquire(
        _CLI_SESSION_KEY,
        db_path=DEFAULT_DB_PATH,
        provider_overrides=provider_overrides,
    )
    try:
        yield agent
    finally:
        await AgentRegistry.aclose_all()
        await ClientCache.aclose_all()
        await dispose_db()

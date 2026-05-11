"""CLI 运行时 helper:进程级 AIAgent 装载 / 释放(0.6.5 起走 AgentRegistry)。

每个 `chariot <cmd>` 调用都是独立 OS 进程,生命周期里需要:
1. 读 `~/.chariot/chariot.db`(跑 migrations + seed 兜底)
2. 通过 `AgentRegistry.reserve(session_key="process", ...)` 拿 AIAgent
3. 命令执行
4. 释放:`AgentRegistry.clear()` + `ClientCache.aclose_all()` + `dispose_db()`

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
- 撤 `AIAgent.uninstall()`(单例已撤);改 `AgentRegistry.clear()`
- 加 `provider_overrides` 参数,传给 AgentRegistry → 一次性 merge 进 entry.options
- 退出顺序:AgentRegistry → ClientCache → DB(从上层释放到底层)

不在 module 顶层放单例 —— 跟 CLAUDE.md ⭐"模块级零自由函数 + 零可变变量"
保持一致;`installed_runtime` 是 async context manager,内部所有状态挂在
AgentRegistry / ClientCache / DBState 这些类的 ClassVar 上。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import ClassVar

from chariot.agent.registry import AgentRegistry
from chariot.agent.run import AIAgent
from chariot.database.session import DEFAULT_DB_PATH, dispose_db
from chariot.providers.clients import ClientCache

# CLI 是单一 session(整个进程),固定 session_key
_CLI_SESSION_KEY = "process"


class CliYoloState:
    """CLI 全局 `--yolo` flag 的进程级状态(B5 wave 3)。

    `chariot --yolo <cmd>` 由 `_root` callback 在子命令运行前 set;子命令通过
    `installed_runtime()` 读它,透传给 `AgentRegistry.reserve(yolo=...)`。

    封装:挂 ClassVar,跟 `Renderer.QUIET` 同模式;模块级零可变变量。
    """

    yolo: ClassVar[bool] = False


@asynccontextmanager
async def installed_runtime(
    *,
    provider_overrides: dict[str, dict[str, str]] | None = None,
    yolo: bool | None = None,
) -> AsyncGenerator[AIAgent, None]:
    """进程级 AIAgent + DB 连接池 + httpx client 缓存的生命周期。

    `provider_overrides`(0.6.5 起):per-process 注入到 entry.options 的 patch,
    给 CLI `--base-url` / `--api-key` 用;传给 `AgentRegistry.reserve`。

    `yolo`(B5 wave 3):per-process 临时 capability 覆盖。None 时回退到全局 flag
    `CliYoloState.yolo`(由 `chariot --yolo <cmd>` 的 `_root` 设置)。

    退出时即使有异常也按 AgentRegistry → ClientCache → DB 顺序释放(防 fixture
    串味 / 连接泄露 / engine 残留)。
    """
    yolo_resolved = CliYoloState.yolo if yolo is None else yolo
    agent = await AgentRegistry.reserve(
        _CLI_SESSION_KEY,
        db_path=DEFAULT_DB_PATH,
        provider_overrides=provider_overrides,
        yolo=yolo_resolved,
    )
    try:
        yield agent
    finally:
        await AgentRegistry.clear()
        await ClientCache.aclose_all()
        await dispose_db()


# ---------------------------------------------------------------------------
# 等价的类形式(对照参考,**未启用**)
# ---------------------------------------------------------------------------
#
# 上面 `@asynccontextmanager + 生成器函数` 是 idiomatic Python;下面是同语义的
# 类形式(展开 `@asynccontextmanager` 糖)。两者用法 1:1 等价:
#
#     async with CliRuntime(provider_overrides=overrides) as agent:
#         ...  # 命令业务
#
# 行为差异:无 —— `__aenter__` / `__aexit__` 跟 yield 前 / yield 后 finally 段
# 一一对应。
#
# 取舍:
# - 类形式适合需要扩展(子类化 / 加 metric / 加 log 钩子)的场景,模板代码 +10 行
# - 生成器形式更紧凑(单一函数体里看完装载 + 释放),chariot 当前用这版
#
# class CliRuntime:
#     """`installed_runtime` 的等价类形式;行为相同,语法不同。"""
#
#     def __init__(
#         self,
#         *,
#         provider_overrides: dict[str, dict[str, str]] | None = None,
#     ) -> None:
#         self.provider_overrides = provider_overrides
#         self.agent: AIAgent | None = None
#
#     async def __aenter__(self) -> AIAgent:
#         self.agent = await AgentRegistry.reserve(
#             _CLI_SESSION_KEY,
#             db_path=DEFAULT_DB_PATH,
#             provider_overrides=self.provider_overrides,
#         )
#         return self.agent
#
#     async def __aexit__(self, *exc: object) -> bool:
#         await AgentRegistry.clear()
#         await ClientCache.aclose_all()
#         await dispose_db()
#         return False  # 不吞异常,沿用 finally 等价语义

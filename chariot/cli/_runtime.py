"""CLI 运行时 helper:进程级 AIAgent 装载 / 释放。

每个 `chariot <cmd>` 调用都是独立 OS 进程,生命周期里需要:
1. 读 `~/.chariot/chariot.db`(跑 migrations + seed 兜底)
2. 装 AIAgent 单例(`AIAgent.from_db`)
3. 命令执行
4. 释放连接池(`dispose_db`)+ 清单例(`AIAgent.uninstall`)

`installed_runtime()` 把这套打成 `async with` 块。子命令 typer wrapper 用法::

    asyncio.run(_run(...))

    async def _run(...) -> None:
        async with installed_runtime() as agent:
            ...  # 命令业务

不在 module 顶层放单例 —— 跟 CLAUDE.md ⭐"模块级零自由函数 + 零可变变量"
保持一致;`installed_runtime` 是 async context manager(类上挂的也行,
但单一函数更顺手 + caller import 一次就够)。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from chariot.agent.run import AIAgent
from chariot.database.session import DEFAULT_DB_PATH, dispose_db


@asynccontextmanager
async def installed_runtime() -> AsyncIterator[AIAgent]:
    """进程级 AIAgent + DB 连接池的生命周期。

    退出时即使有异常也释放连接池并清单例(避免长期 fixture 测试场景里
    `AIAgent._current` 串味)。
    """
    agent = await AIAgent.from_db(DEFAULT_DB_PATH)
    try:
        yield agent
    finally:
        await dispose_db()
        AIAgent.uninstall()

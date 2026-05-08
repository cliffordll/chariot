"""`chariot status` — 显示当前 chariot 装载状态。

0.6.0 库化版:撤 daemon `start / stop`,无 server "running / not running"
概念。改为展示进程加载 AIAgent 时能拿到的信息:
- chariot 版本
- DB 路径
- 默认 provider(`chariot chat` 不传 `--model` 时用的那个)
- 已注册的 providers / tools 数量

**关于"默认 provider"**:0.3.1 起删除了"全局 active"状态(原因:client 在
`body.model` 写 entry name 直接路由,server 不持有 active);CLI 库化后唯一
能称为"当前"的是 `DEFAULT_MODEL` 常量(`chariot chat --model` flag 的默认值)。
要切默认值需要改代码,不是运行时配置。
"""

from __future__ import annotations

import asyncio

import typer

from chariot import __version__
from chariot.cli._runtime import installed_runtime
from chariot.cli.context import DEFAULT_MODEL
from chariot.cli.render import Renderer
from chariot.database.session import DEFAULT_DB_PATH


def status_cmd() -> None:
    """显示 chariot 装载快照(version / DB / 默认 provider / providers / tools)。"""
    asyncio.run(_run())


async def _run() -> None:
    async with installed_runtime() as agent:
        provider_names = ", ".join(sorted(agent.providers))
        tool_names = ", ".join(sorted(agent.tools)) or "(无 enabled)"
        registered = DEFAULT_MODEL in agent.providers
        default_marker = " (registered)" if registered else " (NOT registered)"
        Renderer.kv(
            {
                "version": __version__,
                "db": str(DEFAULT_DB_PATH),
                "default provider": f"{DEFAULT_MODEL}{default_marker}",
                "providers": f"{len(agent.providers)} 个 ({provider_names})",
                "tools": f"{len(agent.tools)} 个 ({tool_names})",
            }
        )


def register(app: typer.Typer) -> None:
    app.command(
        "status",
        help="显示 chariot 装载状态(version / DB / 默认 provider / providers / tools)",
    )(status_cmd)

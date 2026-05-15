"""`chariot status` — 显示当前 chariot 装载状态。

0.6.0 库化版:撤 daemon `start / stop`,无 server "running / not running"
概念。改为展示进程加载 AIAgent 时能拿到的信息:
- chariot 版本
- DB 路径
- 当前默认 provider(`settings.default_provider_id` 指向的那条;`chariot chat`
  不传 `--provider` 时用)
- 已注册的 providers / tools 数量

**关于"默认 provider"**:0.8.9 起从 `settings.default_provider_id` 读;无默认是
合法状态(用户未设)。设默认用 `chariot provider use <ref>`。
"""

from __future__ import annotations

import asyncio

import typer

from chariot import __version__
from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.database.session import DEFAULT_DB_PATH
from chariot.services.provider import ProviderService


def status_cmd() -> None:
    """显示 chariot 装载快照(version / DB / 默认 provider / providers / tools)。"""
    asyncio.run(_run())


async def _run() -> None:
    async with installed_runtime() as agent:
        default = await ProviderService(agent).get_default()
        provider_entries = agent.provider_entries
        provider_names = ", ".join(entry.slug for entry in provider_entries)
        tool_names = ", ".join(sorted(agent.tools)) or "(无 enabled)"
        if default is None:
            default_line = "(未设;`chariot provider use <name>` 设一个)"
        else:
            registered = any(entry.id == default.id for entry in provider_entries)
            mark = "" if registered else " (NOT registered)"
            default_line = f"{default.name} ({default.slug}){mark}"
        Renderer.kv(
            {
                "version": __version__,
                "db": str(DEFAULT_DB_PATH),
                "default provider": default_line,
                "providers": f"{len(provider_entries)} 个 ({provider_names})",
                "tools": f"{len(agent.tools)} 个 ({tool_names})",
            }
        )


def register(app: typer.Typer) -> None:
    app.command(
        "status",
        help="显示 chariot 装载状态(version / DB / 默认 provider / providers / tools)",
    )(status_cmd)

"""`chariot capability <subcmd>` —— capability gating CLI(B5 wave 3)。

子命令:
- `chariot capability list`:列已知 capability 状态(已知集:`enable_self_mod` / `yolo`)
- `chariot capability enable <name>`:打开(持久化到 DB)
- `chariot capability disable <name>`:关闭(持久化到 DB)

注意:`yolo` 也可走 CLI 全局 `--yolo` per-process 临时模式(不入 DB);DB 行存在
是给 sidecar / 长进程 / 多 surface 共享。
"""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from chariot.agent.config import ConfigError
from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.repos.capability_repo import CapabilityRepo

capability_app = typer.Typer(
    name="capability",
    help="capability gating(`enable_self_mod` / `yolo`)",
    no_args_is_help=True,
)


@capability_app.command("list", help="列已知 capability 状态")
def list_cmd() -> None:
    asyncio.run(_list())


@capability_app.command("enable", help="enable 一个 capability(写 DB)")
def enable_cmd(
    name: Annotated[str, typer.Argument(help="capability name")],
) -> None:
    asyncio.run(_set(name, True))


@capability_app.command("disable", help="disable 一个 capability(写 DB)")
def disable_cmd(
    name: Annotated[str, typer.Argument(help="capability name")],
) -> None:
    asyncio.run(_set(name, False))


async def _list() -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        entries = await CapabilityRepo(session).list_entries()
    if not entries:
        Renderer.out("(no capability rows)")
        return
    rows = [
        (entry.name, "yes" if entry.enabled else "no", entry.updated_at.isoformat(timespec="seconds"))
        for entry in entries
    ]
    Renderer.table(["name", "enabled", "updated_at"], rows, title="capabilities")


async def _set(name: str, enabled: bool) -> None:
    async with installed_runtime() as agent:
        try:
            async with agent.session_maker() as session:
                entry = await CapabilityRepo(session).set_enabled(name, enabled)
        except ConfigError as exc:
            Renderer.die(str(exc))
            return
    Renderer.out(f"* {entry.name}: {'enabled' if entry.enabled else 'disabled'}")


def register(app: typer.Typer) -> None:
    app.add_typer(capability_app)

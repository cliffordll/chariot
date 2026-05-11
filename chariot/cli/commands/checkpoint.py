"""`chariot checkpoint list` —— Phase 4 最小 checkpoint 验收入口。"""

from __future__ import annotations

import asyncio

import typer

from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.repos.checkpoint_repo import CheckpointRepo

checkpoint_app = typer.Typer(
    name="checkpoint",
    help="查看 checkpoint 基础表(Phase 4 最小骨架)",
    no_args_is_help=True,
)


@checkpoint_app.command("list", help="列出 checkpoints")
def list_cmd() -> None:
    asyncio.run(_list())


async def _list() -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        entries = await CheckpointRepo(session).list_entries()
    if not entries:
        Renderer.out("(没有 checkpoints)")
        return
    rows = [(entry.id, entry.name, entry.kind, entry.target or "") for entry in entries]
    Renderer.table(["id", "name", "kind", "target"], rows, title="checkpoints")


def register(app: typer.Typer) -> None:
    app.add_typer(checkpoint_app)

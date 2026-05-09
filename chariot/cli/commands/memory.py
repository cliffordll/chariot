"""`chariot memory list` —— Phase 4 最小 memory 验收入口。"""

from __future__ import annotations

import asyncio

import typer

from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.repos.memory_repo import MemoryRepo

memory_app = typer.Typer(
    name="memory",
    help="查看 memory 基础表(Phase 4 最小骨架)",
    no_args_is_help=True,
)


@memory_app.command("list", help="列出 memory entries")
def list_cmd() -> None:
    asyncio.run(_list())


async def _list() -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        entries = await MemoryRepo(session).list_entries()
    if not entries:
        Renderer.out("(没有 memory entries)")
        return
    rows = [(entry.id, entry.kind, entry.text) for entry in entries]
    Renderer.table(["id", "kind", "text"], rows, title="memory")


def register(app: typer.Typer) -> None:
    app.add_typer(memory_app)

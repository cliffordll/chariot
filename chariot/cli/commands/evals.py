"""`chariot eval list` —— Phase 4 最小 eval 验收入口。"""

from __future__ import annotations

import asyncio

import typer

from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.repos.eval_repo import EvalRepo

eval_app = typer.Typer(
    name="eval",
    help="查看 eval 基础表(Phase 4 最小骨架)",
    no_args_is_help=True,
)


@eval_app.command("list", help="列出 eval runs")
def list_cmd() -> None:
    asyncio.run(_list())


async def _list() -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        runs = await EvalRepo(session).list_runs()
    if not runs:
        Renderer.out("(没有 eval runs)")
        return
    rows = [(run.id, run.name or "", run.status) for run in runs]
    Renderer.table(["id", "name", "status"], rows, title="eval runs")


def register(app: typer.Typer) -> None:
    app.add_typer(eval_app)

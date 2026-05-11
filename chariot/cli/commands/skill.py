"""`chariot skill list` —— Phase 4 最小 skill 验收入口。"""

from __future__ import annotations

import asyncio

import typer

from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.repos.skill_repo import SkillRepo

skill_app = typer.Typer(
    name="skill",
    help="查看 skill 基础表(Phase 4 最小骨架)",
    no_args_is_help=True,
)


@skill_app.command("list", help="列出 skills")
def list_cmd() -> None:
    asyncio.run(_list())


async def _list() -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        entries = await SkillRepo(session).list_entries()
    if not entries:
        Renderer.out("(没有 skills)")
        return
    rows = [(entry.id, entry.name, "ON" if entry.enabled else "off") for entry in entries]
    Renderer.table(["id", "name", "enabled"], rows, title="skills")


def register(app: typer.Typer) -> None:
    app.add_typer(skill_app)

"""`chariot checkpoint <subcmd>` —— B5 wave 3 三件套 snapshot + rollback。

子命令:
- `chariot checkpoint list`:列所有 checkpoint
- `chariot checkpoint show <id>`:看单条 payload(三段 ok / 落盘路径 / 错误)
- `chariot checkpoint create <name>`:跑三件套 snapshot
- `chariot checkpoint rollback <id>`:三件套反向恢复
- `chariot checkpoint delete <id>`:删 row + 落盘文件(git stash 保留)
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

import typer

from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.repos.checkpoint_repo import CheckpointRepo

checkpoint_app = typer.Typer(
    name="checkpoint",
    help="Checkpoint 三件套(git stash + sqlite backup + config tarball)",
    no_args_is_help=True,
)


@checkpoint_app.command("list", help="列出 checkpoints")
def list_cmd() -> None:
    asyncio.run(_list())


@checkpoint_app.command("show", help="查看单条 checkpoint 详情")
def show_cmd(
    checkpoint_id: Annotated[str, typer.Argument(help="checkpoint id")],
) -> None:
    asyncio.run(_show(checkpoint_id))


@checkpoint_app.command("create", help="创建 checkpoint(三件套 snapshot)")
def create_cmd(
    name: Annotated[str, typer.Argument(help="checkpoint 名称(描述性)")],
) -> None:
    asyncio.run(_create(name))


@checkpoint_app.command("rollback", help="回滚到 checkpoint(三件套反向)")
def rollback_cmd(
    checkpoint_id: Annotated[str, typer.Argument(help="checkpoint id")],
) -> None:
    asyncio.run(_rollback(checkpoint_id))


@checkpoint_app.command("delete", help="删 checkpoint 行 + 落盘文件(git stash 保留)")
def delete_cmd(
    checkpoint_id: Annotated[str, typer.Argument(help="checkpoint id")],
) -> None:
    asyncio.run(_delete(checkpoint_id))


async def _list() -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        entries = await CheckpointRepo(session).list_entries()
    if not entries:
        Renderer.out("(没有 checkpoints)")
        return
    rows = [
        (
            entry.id,
            entry.name,
            entry.kind,
            "ok" if entry.payload.get("git_ok") else "skip",
            "ok" if entry.payload.get("db_ok") else "skip",
            "ok" if entry.payload.get("config_ok") else "skip",
        )
        for entry in entries
    ]
    Renderer.table(["id", "name", "kind", "git", "db", "config"], rows, title="checkpoints")


async def _show(checkpoint_id: str) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        entry = await CheckpointRepo(session).get_entry(checkpoint_id)
    if entry is None:
        Renderer.die(f"checkpoint not found: {checkpoint_id!r}")
        return
    Renderer.kv(
        {
            "id": entry.id,
            "name": entry.name,
            "kind": entry.kind,
            "target": entry.target or "-",
            "created_at": entry.created_at.isoformat(timespec="seconds"),
        }
    )
    Renderer.out("")
    Renderer.out("payload:")
    Renderer.out(json.dumps(entry.payload, ensure_ascii=False, indent=2, sort_keys=True))


async def _create(name: str) -> None:
    async with installed_runtime() as agent:
        manager = agent.checkpoint_manager
        if manager is None:
            Renderer.die("checkpoint manager not available")
            return
        entry = await manager.create(name)
    payload = entry.payload
    Renderer.out(f"+ {entry.id} {entry.name}")
    Renderer.kv(
        {
            "git": "ok" if payload.get("git_ok") else "skip/fail",
            "git_stash_ref": payload.get("git_stash_ref") or "-",
            "db": payload.get("db_path") or "-",
            "config": payload.get("config_path") or "-",
        }
    )
    errors = payload.get("errors") or []
    if errors:
        Renderer.out("")
        Renderer.out("errors:")
        for err in errors:
            Renderer.out(f"  - {err}")


async def _rollback(checkpoint_id: str) -> None:
    async with installed_runtime() as agent:
        manager = agent.checkpoint_manager
        if manager is None:
            Renderer.die("checkpoint manager not available")
            return
        result = await manager.rollback(checkpoint_id)
    Renderer.kv(
        {
            "git": "ok" if result.git_ok else "fail",
            "db": "ok" if result.db_ok else "fail",
            "config": "ok" if result.config_ok else "fail",
        }
    )
    if result.restored:
        Renderer.out("")
        Renderer.out("restored:")
        for item in result.restored:
            Renderer.out(f"  + {item}")
    if result.errors:
        Renderer.out("")
        Renderer.out("errors:")
        for err in result.errors:
            Renderer.out(f"  - {err}")


async def _delete(checkpoint_id: str) -> None:
    async with installed_runtime() as agent:
        manager = agent.checkpoint_manager
        if manager is None:
            Renderer.die("checkpoint manager not available")
            return
        ok = await manager.delete(checkpoint_id)
    if not ok:
        Renderer.die(f"checkpoint not found: {checkpoint_id!r}")
        return
    Renderer.out(f"- {checkpoint_id}")


def register(app: typer.Typer) -> None:
    app.add_typer(checkpoint_app)

"""`chariot toolset` commands.

命名 toolset 实体的 CRUD + 成员管理。设计:docs/tool-profile-design.md。
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

import typer

from chariot.agent.exceptions import ConfigError
from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.repos.toolset_repo import ToolsetRepo
from chariot.services.toolset import ToolsetService

toolset_app = typer.Typer(name="toolset", help="管理 toolset(命名一组 tool 给 agent 引用)", no_args_is_help=True)
members_app = typer.Typer(name="members", help="管理 toolset 成员", no_args_is_help=True)
toolset_app.add_typer(members_app)


def _fmt_dt(value: Any) -> str:
    if value is None:
        return "-"
    if hasattr(value, "isoformat"):
        return value.isoformat(timespec="seconds")
    return str(value)


def _parse_members(raw: str) -> list[str]:
    if not raw.strip():
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_meta(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        Renderer.die(f"meta must be valid JSON: {exc}")
        raise SystemExit(1) from exc
    if not isinstance(data, dict):
        Renderer.die("meta must be a JSON object")
        raise SystemExit(1) from None
    return data


@toolset_app.command("list", help="列出所有 toolset")
def toolset_list_cmd() -> None:
    asyncio.run(_toolset_list())


@toolset_app.command("show", help="查看单个 toolset")
def toolset_show_cmd(
    name: Annotated[str, typer.Argument(help="toolset name")],
) -> None:
    asyncio.run(_toolset_show(name))


@toolset_app.command("add", help="创建 toolset")
def toolset_add_cmd(
    name: Annotated[str, typer.Option("--name", help="toolset name")] = "",
    description: Annotated[str, typer.Option("--description", help="描述")] = "",
    members: Annotated[str, typer.Option("--members", help="逗号分隔的 tool name 列表")] = "",
    meta: Annotated[str, typer.Option("--meta", help="JSON meta")] = "",
) -> None:
    asyncio.run(
        _toolset_add(
            name=name,
            description=description or None,
            members=_parse_members(members),
            meta=meta,
        )
    )


@toolset_app.command("update", help="更新 toolset")
def toolset_update_cmd(
    name: Annotated[str, typer.Argument(help="toolset name")],
    description: Annotated[str, typer.Option("--description", help="描述")] = "",
    members: Annotated[str, typer.Option("--members", help="逗号分隔;给空字符串清空")] = "__UNSET__",
    meta: Annotated[str, typer.Option("--meta", help="JSON meta")] = "",
) -> None:
    asyncio.run(
        _toolset_update(
            name=name,
            description=description or None,
            members=None if members == "__UNSET__" else _parse_members(members),
            meta=meta,
        )
    )


@toolset_app.command("delete", help="删除 toolset(成员级联清理)")
def toolset_delete_cmd(
    name: Annotated[str, typer.Argument(help="toolset name")],
) -> None:
    asyncio.run(_toolset_delete(name))


@members_app.command("add", help="给 toolset 加成员")
def members_add_cmd(
    name: Annotated[str, typer.Argument(help="toolset name")],
    tool_name: Annotated[str, typer.Argument(help="tool name")],
) -> None:
    asyncio.run(_members_add(name, tool_name))


@members_app.command("delete", help="从 toolset 删成员")
def members_delete_cmd(
    name: Annotated[str, typer.Argument(help="toolset name")],
    tool_name: Annotated[str, typer.Argument(help="tool name")],
) -> None:
    asyncio.run(_members_delete(name, tool_name))


async def _toolset_list() -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        entries = await ToolsetService(ToolsetRepo(session)).list_entries()
    if not entries:
        Renderer.out("(没有 toolset)")
        return
    rows = [
        (
            entry.name,
            entry.description or "-",
            ", ".join(entry.members) if entry.members else "-",
        )
        for entry in entries
    ]
    Renderer.table(["name", "description", "members"], rows, title="toolsets")


async def _toolset_show(name: str) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        entry = await ToolsetService(ToolsetRepo(session)).get_entry(name)
    if entry is None:
        Renderer.die(f"toolset not found: {name!r}")
        return
    Renderer.kv(
        {
            "name": entry.name,
            "description": entry.description or "-",
            "members": ", ".join(entry.members) if entry.members else "(空)",
            "created_at": _fmt_dt(entry.created_at),
            "updated_at": _fmt_dt(entry.updated_at),
        }
    )
    Renderer.out("")
    Renderer.out("meta:")
    Renderer.out(json.dumps(entry.meta, ensure_ascii=False, indent=2, sort_keys=True))


async def _toolset_add(
    *,
    name: str,
    description: str | None,
    members: list[str],
    meta: str,
) -> None:
    if not name.strip():
        Renderer.die("--name is required")
        return
    parsed_meta = _parse_meta(meta) or {}
    async with installed_runtime() as agent, agent.session_maker() as session:
        try:
            entry = await ToolsetService(ToolsetRepo(session)).create(
                name=name.strip(),
                description=description,
                members=members,
                meta=parsed_meta,
            )
        except ConfigError as exc:
            Renderer.die(str(exc))
            return
    Renderer.out(f"+ {entry.name} (members={len(entry.members)})")


async def _toolset_update(
    *,
    name: str,
    description: str | None,
    members: list[str] | None,
    meta: str,
) -> None:
    parsed_meta = _parse_meta(meta) if meta.strip() else None
    async with installed_runtime() as agent, agent.session_maker() as session:
        try:
            entry = await ToolsetService(ToolsetRepo(session)).update(
                name,
                description=description,
                members=members,
                meta=parsed_meta,
            )
        except ConfigError as exc:
            Renderer.die(str(exc))
            return
    Renderer.out(f"~ {entry.name} (members={len(entry.members)})")


async def _toolset_delete(name: str) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        try:
            await ToolsetService(ToolsetRepo(session)).delete(name)
        except ConfigError as exc:
            Renderer.die(str(exc))
            return
    Renderer.out(f"- {name}")


async def _members_add(name: str, tool_name: str) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        try:
            entry = await ToolsetService(ToolsetRepo(session)).add_member(name, tool_name)
        except ConfigError as exc:
            Renderer.die(str(exc))
            return
    Renderer.out(f"+ {name}.{tool_name} (members={len(entry.members)})")


async def _members_delete(name: str, tool_name: str) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        try:
            entry = await ToolsetService(ToolsetRepo(session)).remove_member(name, tool_name)
        except ConfigError as exc:
            Renderer.die(str(exc))
            return
    Renderer.out(f"- {name}.{tool_name} (members={len(entry.members)})")


def register(app: typer.Typer) -> None:
    app.add_typer(toolset_app)

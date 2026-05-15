"""`chariot agent` commands."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

import typer

from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.models.agent import UNSET, ClearableStr
from chariot.services.agent import AgentService

agent_app = typer.Typer(name="agent", help="管理 agent profiles", no_args_is_help=True)


def _fmt_dt(value: Any) -> str:
    if value is None:
        return "-"
    if hasattr(value, "isoformat"):
        return value.isoformat(timespec="seconds")
    return str(value)


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


@agent_app.command("list", help="列出 agent profiles")
def agent_list_cmd() -> None:
    asyncio.run(_agent_list())


@agent_app.command("show", help="查看单个 agent profile")
def agent_show_cmd(
    name: Annotated[str, typer.Argument(help="agent profile name or id")],
) -> None:
    asyncio.run(_agent_show(name))


@agent_app.command("add", help="创建 agent profile")
def agent_add_cmd(
    name: Annotated[str, typer.Option("--name", help="agent profile name (stable id auto-generated)")] = "",
    role: Annotated[str, typer.Option("--role", help="agent role")] = "",
    prompt_bundle: Annotated[str, typer.Option("--prompt-bundle", help="prompt bundle")] = "",
    tool_profile: Annotated[str, typer.Option("--tool-profile", help="tool profile")] = "",
    provider_id: Annotated[
        str,
        typer.Option(
            "--provider-id",
            "--provider-profile",
            help="provider 绑定(推荐 --provider-id; --provider-profile 保留兼容)",
        ),
    ] = "",
    budget: Annotated[str, typer.Option("--budget", help="JSON budget")] = "",
    meta: Annotated[str, typer.Option("--meta", help="JSON meta")] = "",
    reflection_enabled: Annotated[
        bool,
        typer.Option(
            "--reflection-on/--reflection-off",
            help="B4 wave 3:开 reflect-then-retry(需 critic 装载)",
        ),
    ] = False,
    reflect_retries: Annotated[
        int,
        typer.Option("--reflect-retries", help="reflection 最多重试次数(默认 2)"),
    ] = 2,
) -> None:
    asyncio.run(
        _agent_add(
            name=name,
            role=role,
            prompt_bundle=prompt_bundle or None,
            tool_profile=tool_profile or None,
            provider_id=provider_id or None,
            budget=budget,
            meta=meta,
            reflection_enabled=reflection_enabled,
            reflection_max_retries=reflect_retries,
        )
    )


@agent_app.command("update", help="update agent profile")
def agent_update_cmd(
    name: Annotated[str, typer.Argument(help="agent profile name or id")],
    role: Annotated[str, typer.Option("--role", help="agent role")] = "",
    prompt_bundle: Annotated[
        str | None,
        typer.Option("--prompt-bundle", help="prompt bundle name(传空串 `--prompt-bundle ''` 表示清空)"),
    ] = None,
    tool_profile: Annotated[
        str | None,
        typer.Option("--tool-profile", help="tool profile name(传空串清空)"),
    ] = None,
    provider_id: Annotated[
        str | None,
        typer.Option(
            "--provider-id",
            "--provider-profile",
            help="provider 绑定(推荐 --provider-id;传空串清空; --provider-profile 保留兼容)",
        ),
    ] = None,
    budget: Annotated[str, typer.Option("--budget", help="JSON budget")] = "",
    meta: Annotated[str, typer.Option("--meta", help="JSON meta")] = "",
    reflection_on: Annotated[
        bool,
        typer.Option("--reflection-on", help="B4 wave 3:打开 reflection"),
    ] = False,
    reflection_off: Annotated[
        bool,
        typer.Option("--reflection-off", help="B4 wave 3:关闭 reflection"),
    ] = False,
    reflect_retries: Annotated[
        int | None,
        typer.Option("--reflect-retries", help="新的 reflection_max_retries 值"),
    ] = None,
) -> None:
    if reflection_on and reflection_off:
        Renderer.die("--reflection-on 和 --reflection-off 互斥")
        return
    reflection_enabled: bool | None
    if reflection_on:
        reflection_enabled = True
    elif reflection_off:
        reflection_enabled = False
    else:
        reflection_enabled = None
    asyncio.run(
        _agent_update(
            name=name,
            role=role or None,
            prompt_bundle=_to_clearable(prompt_bundle),
            tool_profile=_to_clearable(tool_profile),
            provider_id=_to_clearable(provider_id),
            budget=budget,
            meta=meta,
            reflection_enabled=reflection_enabled,
            reflection_max_retries=reflect_retries,
        )
    )


def _to_clearable(value: str | None) -> ClearableStr:
    """CLI flag → ClearableStr 三态。

    - 没传 flag(typer 给 None)→ UNSET(skip update)
    - `--xxx ''`(显式空串)→ None(清空字段)
    - `--xxx value` → "value"(set)
    """
    if value is None:
        return UNSET
    if value == "":
        return None
    return value


@agent_app.command("remove", help="remove agent profile")
def agent_remove_cmd(
    name: Annotated[str, typer.Argument(help="agent profile name or id")],
) -> None:
    asyncio.run(_agent_remove(name))


async def _agent_list() -> None:
    async with installed_runtime() as agent:
        entries = await AgentService(agent).list_agents()
    if not entries:
        Renderer.out("(没有 agent profiles)")
        return
    rows = [
        (
            entry.id or "-",
            entry.name,
            entry.role,
            entry.prompt_bundle or "-",
            entry.tool_profile or "-",
            entry.provider_id or "-",
        )
        for entry in entries
    ]
    Renderer.table(["id", "name", "role", "prompt_bundle", "tool_profile", "provider_id"], rows, title="agents")


async def _agent_show(name: str) -> None:
    async with installed_runtime() as agent:
        entry = await AgentService(agent).get_agent(name)
        if entry is None:
            Renderer.die(f"agent profile not found: {name!r}")
            return
    Renderer.kv(
        {
            "id": entry.id or "-",
            "name": entry.name,
            "role": entry.role,
            "prompt_bundle": entry.prompt_bundle or "-",
            "tool_profile": entry.tool_profile or "-",
            "toolset_id": entry.toolset_id or "-",
            "provider_id": entry.provider_id or "-",
            "reflection_enabled": str(entry.reflection_enabled).lower(),
            "reflection_max_retries": str(entry.reflection_max_retries),
            "created_at": _fmt_dt(entry.created_at),
            "updated_at": _fmt_dt(entry.updated_at),
        }
    )
    Renderer.out("")
    Renderer.out("budget:")
    Renderer.out(json.dumps(entry.budget, ensure_ascii=False, indent=2, sort_keys=True))
    Renderer.out("")
    Renderer.out("meta:")
    Renderer.out(json.dumps(entry.meta, ensure_ascii=False, indent=2, sort_keys=True))


async def _agent_add(
    *,
    name: str,
    role: str,
    prompt_bundle: str | None,
    tool_profile: str | None,
    provider_id: str | None,
    budget: str,
    meta: str,
    reflection_enabled: bool = False,
    reflection_max_retries: int = 2,
) -> None:
    if not name.strip() or not role.strip():
        Renderer.die("--name and --role are required")
        return
    parsed_budget = _parse_meta(budget) or {}
    parsed_meta = _parse_meta(meta) or {}
    async with installed_runtime() as agent:
        entry = await AgentService(agent).create_agent(
            name=name.strip(),
            role=role.strip(),
            prompt_bundle=prompt_bundle,
            tool_profile=tool_profile,
            provider_id=provider_id,
            budget=parsed_budget,
            meta=parsed_meta,
            reflection_enabled=reflection_enabled,
            reflection_max_retries=reflection_max_retries,
        )
    Renderer.out(f"+ {entry.name} {entry.role}")


async def _agent_update(
    *,
    name: str,
    role: str | None,
    prompt_bundle: ClearableStr,
    tool_profile: ClearableStr,
    provider_id: ClearableStr,
    budget: str,
    meta: str,
    reflection_enabled: bool | None = None,
    reflection_max_retries: int | None = None,
) -> None:
    parsed_budget = _parse_meta(budget) if budget.strip() else None
    parsed_meta = _parse_meta(meta) if meta.strip() else None
    async with installed_runtime() as agent:
        service = AgentService(agent)
        try:
            entry = await service.update_agent(
                name=name,
                role=role,
                prompt_bundle=prompt_bundle,
                tool_profile=tool_profile,
                provider_id=provider_id,
                budget=parsed_budget,
                meta=parsed_meta,
                reflection_enabled=reflection_enabled,
                reflection_max_retries=reflection_max_retries,
            )
        except ValueError as exc:
            Renderer.die(str(exc))
            return
    Renderer.out(f"~ {entry.name} {entry.role}")


async def _agent_remove(name: str) -> None:
    async with installed_runtime() as agent:
        service = AgentService(agent)
        try:
            await service.delete_agent(name)
        except ValueError as exc:
            Renderer.die(str(exc))
            return
    Renderer.out(f"- {name}")


def register(app: typer.Typer) -> None:
    app.add_typer(agent_app)

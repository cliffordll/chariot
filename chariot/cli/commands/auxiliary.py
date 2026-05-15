"""`chariot auxiliary <subcmd>` —— Auxiliary client(副 model)管理(B3 wave 2)。

设计:Auxiliary client 是"指向某个现有 provider + 独立 model + 独立 params"
的 wrapper,给 Context auto-compression 这类"副任务调 LLM"的场景独立路由 + 独立预算。

子命令:list / show / add / update / delete。`-p key=value` 重复进 params dict。
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

import typer

from chariot.agent.exceptions import (
    AuxiliaryClientNotFound,
    ConfigError,
    DuplicateAuxiliaryClientName,
)
from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.services.auxiliary import AuxiliaryService

auxiliary_app = typer.Typer(
    name="auxiliary",
    help="管理 auxiliary clients(副 model)",
    no_args_is_help=True,
)


def _parse_params(raw: list[str]) -> dict[str, object]:
    """`-p k=v` 重复 → dict。value 解析:bool/数字/JSON/字符串。"""
    out: dict[str, object] = {}
    for kv in raw:
        if "=" not in kv:
            raise typer.BadParameter(f"无效的 -p 参数 {kv!r};期望 key=value")
        k, v = kv.split("=", 1)
        out[k] = _coerce_value(v)
    return out


def _coerce_value(v: str) -> object:
    if v.lower() in {"true", "false"}:
        return v.lower() == "true"
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    if v.startswith(("{", "[")):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            pass
    return v


# ---------- list ----------


@auxiliary_app.command("list", help="列 auxiliary clients")
def list_cmd() -> None:
    asyncio.run(_list())


async def _list() -> None:
    async with installed_runtime() as agent:
        entries = await AuxiliaryService(agent).list_entries()
    if not entries:
        Renderer.out("(没有 auxiliary client)")
        return
    rows = [
        (e.id or "-", e.name, e.provider_id, e.model or "-", json.dumps(e.params, ensure_ascii=False)) for e in entries
    ]
    Renderer.table(["id", "name", "provider_id", "model", "params"], rows, title="auxiliary clients")


# ---------- show ----------


@auxiliary_app.command("show", help="详情")
def show_cmd(
    name: Annotated[str, typer.Argument(help="auxiliary client name or id")],
) -> None:
    asyncio.run(_show(name))


async def _show(name: str) -> None:
    async with installed_runtime() as agent:
        entry = await AuxiliaryService(agent).get_entry(name)
    if entry is None:
        Renderer.die(f"未知 auxiliary client ref: {name!r}")
        return
    Renderer.out(f"id:             {entry.id or '-'}")
    Renderer.out(f"name:           {entry.name}")
    Renderer.out(f"provider_id:    {entry.provider_id}")
    Renderer.out(f"model:          {entry.model or '(继承 provider)'}")
    Renderer.out(f"params:         {json.dumps(entry.params, ensure_ascii=False)}")


# ---------- add ----------


@auxiliary_app.command("add", help="新增 auxiliary client")
def add_cmd(
    name: Annotated[str, typer.Option("--name", "-n", help="business name (e.g. summarizer)")],
    provider: Annotated[str, typer.Option("--provider", help="provider 引用(优先 slug / id,兼容 legacy name)")],
    model: Annotated[
        str,
        typer.Option("--model", help="LLM model id;留空 = 继承 provider"),
    ] = "",
    params: Annotated[
        list[str],
        typer.Option("--param", "-p", help="params kv;可重复;value 可为 int/float/bool/JSON"),
    ] = [],
) -> None:
    asyncio.run(_add(name, provider, model or None, _parse_params(params)))


async def _add(
    name: str,
    provider: str,
    model: str | None,
    params: dict[str, object],
) -> None:
    async with installed_runtime() as agent:
        try:
            entry = await AuxiliaryService(agent).create(
                name=name,
                provider_id=provider,
                model=model,
                params=params,
            )
        except DuplicateAuxiliaryClientName as e:
            Renderer.die(str(e))
            return
        except ConfigError as e:
            Renderer.die(str(e))
            return
    Renderer.out(f"+ {entry.name} → {entry.provider_id}")


# ---------- update ----------


@auxiliary_app.command("update", help="改 provider 绑定 / model / params")
def update_cmd(
    name: Annotated[str, typer.Argument(help="auxiliary client name or id")],
    provider: Annotated[
        str,
        typer.Option("--provider", help="新 provider 引用(优先 slug / id,兼容 legacy name)"),
    ] = "",
    model: Annotated[
        str,
        typer.Option("--model", help="新 model id;`--model -` 表示 clear"),
    ] = "",
    params: Annotated[
        list[str],
        typer.Option("--param", "-p", help="新 params kv;可重复(整体替换)"),
    ] = [],
) -> None:
    asyncio.run(_update(name, provider, model, params))


async def _update(name: str, provider: str, model_arg: str, params_raw: list[str]) -> None:
    from chariot.models.agent import UNSET, ClearableStr

    model: ClearableStr = UNSET
    if model_arg == "-":
        model = None
    elif model_arg:
        model = model_arg
    params = _parse_params(params_raw) if params_raw else None
    async with installed_runtime() as agent:
        try:
            entry = await AuxiliaryService(agent).update(
                name,
                provider_id=provider or None,
                model=model,
                params=params,
            )
        except AuxiliaryClientNotFound as e:
            Renderer.die(str(e))
            return
        except ConfigError as e:
            Renderer.die(str(e))
            return
    Renderer.out(f"~ {entry.name} → {entry.provider_id}")


# ---------- delete ----------


@auxiliary_app.command("delete", help="删除 auxiliary client")
@auxiliary_app.command("rm", help="`delete` 的兼容别名")
def delete_cmd(
    name: Annotated[str, typer.Argument(help="auxiliary client name or id")],
) -> None:
    asyncio.run(_delete(name))


async def _delete(name: str) -> None:
    async with installed_runtime() as agent:
        try:
            await AuxiliaryService(agent).delete(name)
        except AuxiliaryClientNotFound as e:
            Renderer.die(str(e))
            return
    Renderer.out(f"- {name}")


def register(app: typer.Typer) -> None:
    app.add_typer(auxiliary_app)

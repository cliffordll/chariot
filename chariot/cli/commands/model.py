"""`chariot model list / probe / add / edit / rm / duplicate` —— 模型 CRUD。

二级子命令组(typer.Typer 嵌套):

只读:
- `chariot model list`:列出 entries(name / type)+ 已注册 type
- `chariot model probe <name>`:发 1 条最小请求验通断(**~1 token 费用**;mock 零费用)

CRUD(0.3.0 加,0.3.1 加 --params):
- `chariot model add --name X --type Y [-o k=v] [-p k=v]`:新建 entry
- `chariot model edit <name> [--type T] [-o k=v] [-p k=v]`:改 entry
- `chariot model rm <name>`:删 entry
- `chariot model duplicate <name> [--as new-name]`:复制(碰撞自动 _copy_N)

0.3.1 路由模型重构后 `chariot model use` 删除(active 概念退役);
client 在请求 body.model 写 entry name 直接路由。

`-o key=value` / `-p key=value` 都可重复;value 全部按字符串处理(0.3.1 不支持嵌套)。
server 未运行 → 错误退出(不自动 spawn,这些都是对运行中 server 的操作)。
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

import httpx
import typer

from chariot.cli.core.render import Renderer
from chariot.sdk.client import ProxyClient

model_app = typer.Typer(
    name="model",
    help="管理 model entries(0.3.1 起按 entry name 路由)",
    no_args_is_help=True,
)


# ---------- list ----------


@model_app.command("list", help="列出 entries(name / type)")
def list_cmd() -> None:
    asyncio.run(_list())


async def _list() -> None:
    try:
        async with ProxyClient.discover_session(spawn_if_missing=False) as client:
            data = await client.list_models()
    except RuntimeError:
        Renderer.die("server 未运行;先 `chariot start` 起一份")
        return

    if not data.entries:
        Renderer.out("(DB 里没有 entry — `chariot model add` 加一条)")
    else:
        rows = [(e.name, e.type) for e in data.entries]
        Renderer.table(["name", "type"], rows, title="entries")

    Renderer.out(f"已注册 type:{', '.join(data.types)}")


# ---------- probe ----------


@model_app.command("probe", help="探一下指定 model 通不通(消耗 ~1 token 费用)")
def probe_cmd(
    name: Annotated[str, typer.Argument(help="entry name")],
) -> None:
    asyncio.run(_probe(name))


async def _probe(name: str) -> None:
    try:
        async with ProxyClient.discover_session(spawn_if_missing=False) as client:
            try:
                result = await client.probe_model(name)
            except httpx.HTTPStatusError as e:
                Renderer.die(f"probe 失败 (HTTP {e.response.status_code}): {e.response.text}")
                return
    except RuntimeError:
        Renderer.die("server 未运行;先 `chariot start` 起一份")
        return

    if result.ok:
        Renderer.out(f"✓ {name} OK ({result.latency_ms} ms)")
    else:
        err = result.error
        code = err.code if err else "unknown"
        message = err.message if err else "(no detail)"
        Renderer.die(f"✗ {name} FAIL ({result.latency_ms} ms) [{code}] {message}")


def _parse_kv(items: list[str], *, label: str) -> dict[str, Any]:
    """`-x key=value` 重复 → dict;value 拆第一个 `=` 后保留(支持值含 `=`)。"""
    result: dict[str, Any] = {}
    for raw in items:
        if "=" not in raw:
            Renderer.die(f"{label} 必须是 key=value 形式: {raw!r}")
            return {}
        k, _, v = raw.partition("=")
        if not k:
            Renderer.die(f"{label} key 不能为空: {raw!r}")
            return {}
        result[k] = v
    return result


# ---------- add ----------


@model_app.command("add", help="新建 model entry(写入 DB)")
def add_cmd(
    name: Annotated[str, typer.Option("--name", help="entry 名(用户面 ID,需唯一)")],
    type: Annotated[str, typer.Option("--type", help="model type(mock / anthropic / ...)")],
    options: Annotated[
        list[str] | None,
        typer.Option(
            "-o", "--option", help="options key=value;可重复(build Model 所需,如 model / api_key)"
        ),
    ] = None,
    params: Annotated[
        list[str] | None,
        typer.Option(
            "-p", "--param", help="params key=value;可重复(runtime 默认值,如 temperature)"
        ),
    ] = None,
) -> None:
    asyncio.run(_add(name, type, options or [], params or []))


async def _add(name: str, type_: str, options: list[str], params: list[str]) -> None:
    opts = _parse_kv(options, label="-o")
    prms = _parse_kv(params, label="-p")
    try:
        async with ProxyClient.discover_session(spawn_if_missing=False) as client:
            try:
                entry = await client.create_model(
                    name=name,
                    type=type_,
                    options=opts,
                    params=prms,
                )
            except httpx.HTTPStatusError as e:
                Renderer.die(f"添加失败 (HTTP {e.response.status_code}): {e.response.text}")
                return
    except RuntimeError:
        Renderer.die("server 未运行;先 `chariot start` 起一份")
        return
    Renderer.out(f"+ {entry.name} (type={entry.type})")


# ---------- edit ----------


@model_app.command("edit", help="编辑现有 entry(改 type / options / params;改完立即 rebuild)")
def edit_cmd(
    name: Annotated[str, typer.Argument(help="要改的 entry 名")],
    type: Annotated[
        str | None,
        typer.Option("--type", help="新 type(可选)"),
    ] = None,
    options: Annotated[
        list[str] | None,
        typer.Option(
            "-o",
            "--option",
            help="options key=value;可重复(整体替换 options,非 merge)",
        ),
    ] = None,
    params: Annotated[
        list[str] | None,
        typer.Option(
            "-p",
            "--param",
            help="params key=value;可重复(整体替换 params,非 merge)",
        ),
    ] = None,
) -> None:
    asyncio.run(_edit(name, type, options, params))


async def _edit(
    name: str,
    type_: str | None,
    options: list[str] | None,
    params: list[str] | None,
) -> None:
    if type_ is None and options is None and params is None:
        Renderer.die("至少给一个 --type / -o / -p 选项,否则无事可做")
        return
    opts = _parse_kv(options, label="-o") if options is not None else None
    prms = _parse_kv(params, label="-p") if params is not None else None
    try:
        async with ProxyClient.discover_session(spawn_if_missing=False) as client:
            try:
                entry = await client.update_model(
                    name,
                    type=type_,
                    options=opts,
                    params=prms,
                )
            except httpx.HTTPStatusError as e:
                Renderer.die(f"编辑失败 (HTTP {e.response.status_code}): {e.response.text}")
                return
    except RuntimeError:
        Renderer.die("server 未运行;先 `chariot start` 起一份")
        return
    Renderer.out(f"~ {entry.name} (type={entry.type})")


# ---------- rm ----------


@model_app.command("rm", help="删除 entry")
def rm_cmd(
    name: Annotated[str, typer.Argument(help="要删的 entry 名")],
) -> None:
    asyncio.run(_rm(name))


async def _rm(name: str) -> None:
    try:
        async with ProxyClient.discover_session(spawn_if_missing=False) as client:
            try:
                await client.delete_model(name)
            except httpx.HTTPStatusError as e:
                Renderer.die(f"删除失败 (HTTP {e.response.status_code}): {e.response.text}")
                return
    except RuntimeError:
        Renderer.die("server 未运行;先 `chariot start` 起一份")
        return
    Renderer.out(f"- {name}")


# ---------- duplicate ----------


@model_app.command("duplicate", help="复制 entry(--as 缺省自动 _copy / _copy_N)")
def duplicate_cmd(
    name: Annotated[str, typer.Argument(help="要复制的源 entry 名")],
    as_name: Annotated[
        str | None,
        typer.Option("--as", help="新 entry 名;缺省 <name>_copy(碰撞自动 _copy_2 / _3)"),
    ] = None,
) -> None:
    asyncio.run(_duplicate(name, as_name))


async def _duplicate(name: str, as_name: str | None) -> None:
    try:
        async with ProxyClient.discover_session(spawn_if_missing=False) as client:
            try:
                entry = await client.duplicate_model(name, as_name=as_name)
            except httpx.HTTPStatusError as e:
                Renderer.die(f"复制失败 (HTTP {e.response.status_code}): {e.response.text}")
                return
    except RuntimeError:
        Renderer.die("server 未运行;先 `chariot start` 起一份")
        return
    Renderer.out(f"+ {entry.name} (type={entry.type}, copied from {name})")


def register(app: typer.Typer) -> None:
    app.add_typer(model_app)

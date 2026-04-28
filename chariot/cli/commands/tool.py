"""`chariot tool list / enable / disable / config` —— 内置工具配置(0.4.0)。

二级子命令组(typer.Typer 嵌套):

只读:
- `chariot tool list`:列 4 条 fixture(name / type / enabled / options)

写(只允许改 enabled / options;name + type 不可改,数量固定):
- `chariot tool enable <name>`:启用
- `chariot tool disable <name>`:禁用
- `chariot tool config <name> -o key=value [-o ...]`:覆写 options(整体替换,非 merge)

server 未运行 → 错误退出。
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

import httpx
import typer

from chariot.cli.core.render import Renderer
from chariot.sdk.client import ProxyClient

tool_app = typer.Typer(
    name="tool",
    help="管理内置工具(0.4.0 起;4 条 fixture,只能改 enabled / options)",
    no_args_is_help=True,
)


# ---------- list ----------


@tool_app.command("list", help="列出 4 条 fixture")
def list_cmd() -> None:
    asyncio.run(_list())


async def _list() -> None:
    try:
        async with ProxyClient.discover_session(spawn_if_missing=False) as client:
            data = await client.list_tools()
    except RuntimeError:
        Renderer.die("server 未运行;先 `chariot start` 起一份")
        return

    rows = [
        (
            e.name,
            e.type,
            "ON" if e.enabled else "off",
            json.dumps(e.options, ensure_ascii=False),
        )
        for e in data.entries
    ]
    Renderer.table(["name", "type", "enabled", "options"], rows, title="tools")


# ---------- enable / disable ----------


@tool_app.command("enable", help="启用一个工具(Agent 立即 rebuild,LLM 下一轮可见)")
def enable_cmd(
    name: Annotated[
        str,
        typer.Argument(help="工具 name(read_file / list_dir / shell_exec / http_get)"),
    ],
) -> None:
    asyncio.run(_set_enabled(name, True))


@tool_app.command("disable", help="禁用一个工具")
def disable_cmd(
    name: Annotated[str, typer.Argument(help="工具 name")],
) -> None:
    asyncio.run(_set_enabled(name, False))


async def _set_enabled(name: str, enabled: bool) -> None:
    try:
        async with ProxyClient.discover_session(spawn_if_missing=False) as client:
            try:
                tool = await client.update_tool(name, enabled=enabled)
            except httpx.HTTPStatusError as e:
                Renderer.die(f"修改失败 (HTTP {e.response.status_code}): {e.response.text}")
                return
    except RuntimeError:
        Renderer.die("server 未运行;先 `chariot start` 起一份")
        return
    state = "ON" if tool.enabled else "off"
    Renderer.out(f"~ {tool.name}: {state}")


# ---------- config ----------


def _parse_kv(items: list[str]) -> dict[str, Any]:
    """`-o key=value` 重复 → dict;value 拆第一个 `=` 后保留(支持值含 `=`)。

    试 JSON 解析(`0.7` → number / `[\"a\"]` → list / `true` → bool);失败留 string。
    """
    result: dict[str, Any] = {}
    for raw in items:
        if "=" not in raw:
            Renderer.die(f"-o 必须是 key=value 形式: {raw!r}")
            return {}
        k, _, v = raw.partition("=")
        if not k:
            Renderer.die(f"-o key 不能为空: {raw!r}")
            return {}
        try:
            result[k] = json.loads(v)
        except json.JSONDecodeError:
            result[k] = v
    return result


@tool_app.command("config", help="覆写 options(整体替换,非 merge);value 试 JSON 解析")
def config_cmd(
    name: Annotated[str, typer.Argument(help="工具 name")],
    options: Annotated[
        list[str] | None,
        typer.Option("-o", "--option", help="options key=value;可重复"),
    ] = None,
) -> None:
    asyncio.run(_config(name, options or []))


async def _config(name: str, options: list[str]) -> None:
    if not options:
        Renderer.die("至少给一个 -o key=value;否则无事可做")
        return
    opts = _parse_kv(options)
    try:
        async with ProxyClient.discover_session(spawn_if_missing=False) as client:
            try:
                tool = await client.update_tool(name, options=opts)
            except httpx.HTTPStatusError as e:
                Renderer.die(f"修改失败 (HTTP {e.response.status_code}): {e.response.text}")
                return
    except RuntimeError:
        Renderer.die("server 未运行;先 `chariot start` 起一份")
        return
    Renderer.out(f"~ {tool.name}: options={json.dumps(tool.options, ensure_ascii=False)}")


def register(app: typer.Typer) -> None:
    app.add_typer(tool_app)

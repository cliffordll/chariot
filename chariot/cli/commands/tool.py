"""`chariot tool` commands.

Current surfaces:
- `list`: list all seeded tools
- `show <name>`: show one tool's config and schema
- `probe <name>`: validate the current config by rebuilding the tool
- `enable / disable / config`: existing admin actions
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Annotated, Any

import typer

from chariot.agent.exceptions import ConfigError, ToolNotFound
from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.repos.tool_repo import ToolRepo
from chariot.tools.registry import ToolRegistry

tool_app = typer.Typer(
    name="tool",
    help="Manage built-in tools",
    no_args_is_help=True,
)


@tool_app.command("list", help="List all seeded tools")
def list_cmd() -> None:
    asyncio.run(_list())


async def _list() -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        entries = await ToolRepo(session).list_entries()

    rows = [
        (
            e.name,
            e.type,
            "ON" if e.enabled else "off",
            json.dumps(e.options, ensure_ascii=False),
        )
        for e in entries
    ]
    Renderer.table(["name", "type", "enabled", "options"], rows, title="tools")


@tool_app.command("show", help="Show one tool's config and schema")
def show_cmd(
    name: Annotated[str, typer.Argument(help="tool name")],
) -> None:
    asyncio.run(_show(name))


async def _show(name: str) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        entry = await ToolRepo(session).get_entry(name)
    if entry is None:
        Renderer.die(f"unknown tool: {name!r}")
        return

    rows: list[tuple[str, str]] = [
        ("name", entry.name),
        ("type", entry.type),
        ("enabled", "yes" if entry.enabled else "no"),
    ]
    for k, v in entry.options.items():
        rows.append((f"options.{k}", _stringify(v)))

    schema = _tool_schema(entry)
    rows.append(
        (
            "schema",
            json.dumps(schema, ensure_ascii=False, indent=2) if schema else "(invalid config)",
        )
    )
    Renderer.table(["field", "value"], rows, title=f"tool {entry.name}")


@tool_app.command("probe", help="Validate one tool's current config")
def probe_cmd(
    name: Annotated[str, typer.Argument(help="tool name")],
) -> None:
    asyncio.run(_probe(name))


async def _probe(name: str) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        entry = await ToolRepo(session).get_entry(name)
    if entry is None:
        Renderer.die(f"unknown tool: {name!r}")
        return

    start = time.perf_counter()
    try:
        tool = ToolRegistry.build(entry)
        _ = tool.schema()
    except (ConfigError, ValueError, TypeError) as e:
        latency_ms = int((time.perf_counter() - start) * 1000)
        Renderer.die(f"FAIL {name} ({latency_ms} ms) [invalid_tool_config] {e}")
        return

    latency_ms = int((time.perf_counter() - start) * 1000)
    Renderer.out(f"OK {name} ({latency_ms} ms)")


@tool_app.command("enable", help="Enable one tool")
def enable_cmd(
    name: Annotated[str, typer.Argument(help="tool name")],
) -> None:
    asyncio.run(_set_enabled(name, True))


@tool_app.command("disable", help="Disable one tool")
def disable_cmd(
    name: Annotated[str, typer.Argument(help="tool name")],
) -> None:
    asyncio.run(_set_enabled(name, False))


async def _set_enabled(name: str, enabled: bool) -> None:
    async with installed_runtime() as agent:
        try:
            async with agent.session_maker() as session:
                tool = await ToolRepo(session).update(name, enabled=enabled)
        except ToolNotFound as e:
            Renderer.die(f"update failed: {e}")
            return
    state = "ON" if tool.enabled else "off"
    Renderer.out(f"~ {tool.name}: {state}")


def _parse_kv(items: list[str]) -> dict[str, Any]:
    """Parse `-o key=value` items into a dict.

    Accepts a legacy comma-separated `key:value,key:value` form too.
    JSON-looking blobs are rejected here; `provider`/`tool` config is
    supposed to be written as individual items.
    """

    result: dict[str, Any] = {}
    for raw in items:
        text = raw.strip()
        if _looks_like_json(text):
            Renderer.die(_kv_error_message(raw, json_like=True))
            return {}

        parts = [text]
        if "=" not in text and "," in text:
            parts = [part.strip() for part in text.split(",") if part.strip()]

        for part in parts:
            if "=" in part:
                k, _, v = part.partition("=")
            elif ":" in part:
                k, _, v = part.partition(":")
            else:
                Renderer.die(_kv_error_message(raw))
                return {}

            k = k.strip()
            if not k or not _is_valid_kv_key(k):
                Renderer.die(_kv_error_message(raw))
                return {}
            result[k] = _parse_option_value(v.strip())
    return result


def _looks_like_json(raw: str) -> bool:
    return (raw.startswith("{") and raw.endswith("}")) or (
        raw.startswith("[") and raw.endswith("]")
    )


def _is_valid_kv_key(key: str) -> bool:
    return all(ch.isalnum() or ch in {"_", ".", "-"} for ch in key)


def _parse_option_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [part.strip().strip("'\"") for part in inner.split(",") if part.strip()]
    return raw


def _kv_error_message(raw: str, *, json_like: bool = False) -> str:
    msg = (
        f"-o must be key=value or comma-separated key:value pairs: {raw!r}\n"
        'template: chariot tool config http_get -o allowed_domains=["example.com"]\n'
        "compat: -o key:value,key:value,..."
    )
    if json_like:
        return msg + "\nnote: do not pass a whole JSON blob to tool config"
    return msg


@tool_app.command("config", help="Overwrite tool options")
def config_cmd(
    name: Annotated[str, typer.Argument(help="tool name")],
    options: Annotated[
        list[str] | None,
        typer.Option("-o", "--option", help="options key=value; repeatable"),
    ] = None,
) -> None:
    asyncio.run(_config(name, options or []))


async def _config(name: str, options: list[str]) -> None:
    if not options:
        Renderer.die("at least one -o key=value is required")
        return
    opts = _parse_kv(options)
    async with installed_runtime() as agent:
        try:
            async with agent.session_maker() as session:
                tool = await ToolRepo(session).update(name, options=opts)
        except ToolNotFound as e:
            Renderer.die(f"update failed: {e}")
            return
        except ConfigError as e:
            Renderer.die(f"update failed: {e}")
            return
    Renderer.out(f"~ {tool.name}: options={json.dumps(tool.options, ensure_ascii=False)}")


def _tool_schema(entry: Any) -> dict[str, Any] | None:
    try:
        return ToolRegistry.build(entry).schema()
    except (ConfigError, ValueError, TypeError):
        return None


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def register(app: typer.Typer) -> None:
    app.add_typer(tool_app)

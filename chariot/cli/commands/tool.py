"""`chariot tool` 命令。

当前 surface:
- `list`: 列出所有工具
- `show <name>`: 查看工具配置和 schema
- `probe <name>`: 验证工具当前配置
- `enable / disable / config`: 管理操作
- `add`: 创建自定义工具 (0.8.7); 别名 `create`
- `update <name>`: 更新自定义工具 (0.8.7); 别名 `edit`
- `delete <name>`: 删除自定义工具 (0.8.7); 别名 `del` / `rm` / `remove`
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml

from chariot.agent.exceptions import ConfigError, ToolNotFound
from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.services.tool import ToolService
from chariot.tools.registry import ToolRegistry

tool_app = typer.Typer(
    name="tool",
    help="管理内置和自定义工具",
    no_args_is_help=True,
)


@tool_app.command("list", help="列出所有工具")
def list_cmd() -> None:
    asyncio.run(_list())


async def _list() -> None:
    async with installed_runtime() as agent:
        entries = await ToolService(agent).list_entries()

    rows = [
        (
            e.name,
            e.type,
            e.source,
            "ON" if e.enabled else "off",
            e.description[:30] + "..." if e.description and len(e.description) > 30 else e.description,
        )
        for e in entries
    ]
    Renderer.table(["name", "type", "source", "enabled", "description"], rows, title="tools")


@tool_app.command("show", help="查看工具配置和 schema")
def show_cmd(
    name: Annotated[str, typer.Argument(help="工具名")],
) -> None:
    asyncio.run(_show(name))


async def _show(name: str) -> None:
    async with installed_runtime() as agent:
        entry = await ToolService(agent).get_entry(name)
    if entry is None:
        Renderer.die(f"未知工具: {name!r}")
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
            json.dumps(schema, ensure_ascii=False, indent=2) if schema else "(无效配置)",
        )
    )
    Renderer.table(["field", "value"], rows, title=f"tool {entry.name}")


@tool_app.command("probe", help="验证工具当前配置")
def probe_cmd(
    name: Annotated[str, typer.Argument(help="工具名")],
) -> None:
    asyncio.run(_probe(name))


async def _probe(name: str) -> None:
    async with installed_runtime() as agent:
        entry = await ToolService(agent).get_entry(name)
    if entry is None:
        Renderer.die(f"未知工具: {name!r}")
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


@tool_app.command("enable", help="启用工具")
def enable_cmd(
    name: Annotated[str, typer.Argument(help="工具名")],
) -> None:
    asyncio.run(_set_enabled(name, True))


@tool_app.command("disable", help="禁用工具")
def disable_cmd(
    name: Annotated[str, typer.Argument(help="工具名")],
) -> None:
    asyncio.run(_set_enabled(name, False))


async def _set_enabled(name: str, enabled: bool) -> None:
    async with installed_runtime() as agent:
        try:
            tool = await ToolService(agent).update(name, enabled=enabled)
        except ToolNotFound as e:
            Renderer.die(f"更新失败: {e}")
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
    return (raw.startswith("{") and raw.endswith("}")) or (raw.startswith("[") and raw.endswith("]"))


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
            tool = await ToolService(agent).update(name, options=opts)
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


# ---------- add ----------


@tool_app.command("add", help="Create a custom tool (0.8.7)")
def add_cmd(
    name: Annotated[str, typer.Option("--name", "-n", help="tool name (unique)")],
    type_: Annotated[
        str,
        typer.Option("--type", "-t", help="custom type: http_custom | shell_custom"),
    ],
    description: Annotated[
        str | None,
        typer.Option("--description", "-d", help="tool description"),
    ] = None,
    from_file: Annotated[
        str | None,
        typer.Option("--from-file", "-f", help="YAML config file"),
    ] = None,
    interactive: Annotated[
        bool,
        typer.Option("--interactive", "-i", help="interactive mode (input options YAML)"),
    ] = False,
) -> None:
    asyncio.run(_create(name, type_, description, from_file, interactive))


async def _create(
    name: str,
    type_: str,
    description: str | None,
    from_file: str | None,
    interactive: bool,
) -> None:
    if type_ not in ("http_custom", "shell_custom"):
        Renderer.die(f"type must be http_custom or shell_custom, got {type_!r}")
        return

    desc = description or ""
    options: dict[str, Any] = {}

    if from_file is not None:
        path = Path(from_file)
        if not path.exists():
            Renderer.die(f"file not found: {from_file!r}")
            return
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            Renderer.die(f"YAML parse error: {e}")
            return
        if not isinstance(data, dict):
            Renderer.die("YAML root must be an object")
            return
        desc = description or data.get("description", "")
        options = data.get("options", {})
        if not isinstance(options, dict):
            Renderer.die("options must be an object")
            return

    elif interactive:
        Renderer.out("Enter options as YAML (Ctrl+D / Ctrl+Z to finish):")
        lines: list[str] = []
        try:
            while True:
                line = input()
                lines.append(line)
        except EOFError:
            pass
        try:
            data = yaml.safe_load("\n".join(lines))
        except yaml.YAMLError as e:
            Renderer.die(f"YAML parse error: {e}")
            return
        if data is None:
            data = {}
        if not isinstance(data, dict):
            Renderer.die("YAML root must be an object")
            return
        options = data

    else:
        Renderer.die("One of --from-file or --interactive is required")
        return

    async with installed_runtime() as agent:
        try:
            tool = await ToolService(agent).create(
                name=name,
                type=type_,
                enabled=True,
                options=options,
                source="custom",
                description=desc,
                custom_type=type_,
            )
        except ConfigError as e:
            Renderer.die(f"create failed: {e}")
            return
    Renderer.out(f"+ {tool.name} (type={tool.custom_type}, source=custom)")


# ---------- update ----------


@tool_app.command("update", help="更新自定义工具 (0.8.7)")
def update_cmd(
    name: Annotated[str, typer.Argument(help="工具名")],
    description: Annotated[
        str | None,
        typer.Option("--description", "-d", help="新描述"),
    ] = None,
    from_file: Annotated[
        str | None,
        typer.Option("--from-file", "-f", help="YAML 配置文件 (替换 options)"),
    ] = None,
    options: Annotated[
        list[str] | None,
        typer.Option("-o", "--option", help="options key=value; 可重复"),
    ] = None,
) -> None:
    asyncio.run(_update(name, description, from_file, options or []))


async def _update(
    name: str,
    description: str | None,
    from_file: str | None,
    option_items: list[str],
) -> None:
    async with installed_runtime() as agent:
        entry = await ToolService(agent).get_entry(name)
    if entry is None:
        Renderer.die(f"未知工具: {name!r}")
        return
    if entry.source != "custom":
        Renderer.die(f"内置工具不可更新: {name!r}")
        return

    new_description = description
    new_options: dict[str, Any] | None = None

    if from_file is not None:
        path = Path(from_file)
        if not path.exists():
            Renderer.die(f"文件不存在: {from_file!r}")
            return
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            Renderer.die(f"YAML 解析错误: {e}")
            return
        if not isinstance(data, dict):
            Renderer.die("YAML 根必须是对象")
            return
        new_description = description or data.get("description")
        new_options = data.get("options", {})
        if not isinstance(new_options, dict):
            Renderer.die("options 必须是对象")
            return

    elif option_items:
        new_options = _parse_kv(option_items)

    if new_description is None and new_options is None:
        Renderer.die("至少提供 --description、--from-file 或 -o 之一")
        return

    async with installed_runtime() as agent:
        try:
            tool = await ToolService(agent).update_full(
                name,
                options=new_options,
                description=new_description,
            )
        except ToolNotFound as e:
            Renderer.die(f"更新失败: {e}")
            return
        except ConfigError as e:
            Renderer.die(f"更新失败: {e}")
            return
    Renderer.out(f"~ {tool.name} (type={tool.custom_type})")


# ---------- delete ----------


@tool_app.command("delete", help="删除自定义工具 (0.8.7)")
def delete_cmd(
    name: Annotated[str, typer.Argument(help="工具名")],
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="跳过确认提示"),
    ] = False,
) -> None:
    asyncio.run(_delete(name, yes))


async def _delete(name: str, yes: bool) -> None:
    async with installed_runtime() as agent:
        entry = await ToolService(agent).get_entry(name)
    if entry is None:
        Renderer.die(f"未知工具: {name!r}")
        return
    if entry.source == "builtin":
        Renderer.die(f"内置工具不可删除: {name!r}")
        return

    if not yes:
        print(f"删除自定义工具 '{name}'? [y/N] ", end="")
        try:
            confirm = input().strip().lower()
        except EOFError:
            confirm = "n"
        if confirm != "y":
            Renderer.out("已取消")
            return

    async with installed_runtime() as agent:
        try:
            await ToolService(agent).delete(name)
        except ToolNotFound as e:
            Renderer.die(f"删除失败: {e}")
            return
        except ConfigError as e:
            Renderer.die(f"删除失败: {e}")
            return
    Renderer.out(f"- {name}")


def register(app: typer.Typer) -> None:
    app.add_typer(tool_app)

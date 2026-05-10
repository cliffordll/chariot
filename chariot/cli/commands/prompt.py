"""Prompt 管理命令。

功能分三块：
- 查看：`list` / `show` / `versions` / `version`
- 管理：`add` / `update` / `activate`
- 追踪：`traces` / `inspect`
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

import typer

from chariot.agent.exceptions import ConfigError
from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.repos.prompt_repo import (
    DEFAULT_BUNDLE_LAYERS,
    PromptRepo,
)

_MISSING = object()

prompt_help = (
    "管理 prompt bundle、版本和 trace。\n\n"
    "查看：list、show、versions、version\n"
    "管理：add、update、activate\n"
    "追踪：traces、inspect\n"
)

prompt_app = typer.Typer(
    name="prompt",
    help=prompt_help,
    no_args_is_help=True,
)


def _fmt_dt(value: Any) -> str:
    if value is None:
        return "-"
    if hasattr(value, "isoformat"):
        return value.isoformat(timespec="seconds")
    return str(value)


def _json_text(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _truncate(text: str, limit: int = 80) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _render_bundle_rows(entries: list[Any]) -> None:
    if not entries:
        Renderer.out("(没有 prompt bundle)")
        return
    rows = [
        (
            entry.name,
            "yes" if entry.is_active else "no",
            str(entry.version_count),
            entry.active_version or "-",
            _truncate(entry.description or "-", 48),
        )
        for entry in entries
    ]
    Renderer.table(["bundle", "active", "versions", "current", "description"], rows, title="prompt bundles")


def _render_layers_table(layers: list[dict[str, Any]]) -> None:
    if not layers:
        Renderer.out("(没有 layers)")
        return
    rows = []
    for layer in layers:
        content = layer.get("content")
        if isinstance(content, str):
            rendered = content
        elif content is None:
            rendered = "-"
        else:
            rendered = json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True)
        rows.append(
            (
                layer.get("name", "-"),
                layer.get("source", "-"),
                _truncate(rendered, 120),
            )
        )
    Renderer.table(["layer", "source", "content"], rows, title="layers")


def _render_trace_rows(entries: list[Any]) -> None:
    if not entries:
        Renderer.out("(没有 prompt trace)")
        return
    rows = [
        (
            entry.id,
            entry.bundle_name,
            entry.version,
            entry.conversation_id or "-",
            entry.provider_name,
            entry.model or "-",
            str(entry.prompt_size),
            _fmt_dt(entry.created_at),
        )
        for entry in entries
    ]
    Renderer.table(
        ["trace", "bundle", "version", "conversation", "provider", "model", "size", "created_at"],
        rows,
        title="prompt traces",
    )


def _parse_layers(values: list[str] | None) -> list[dict[str, Any]] | None:
    if values is None:
        return None
    layers: list[dict[str, Any]] = []
    for raw in values:
        text = raw.strip()
        if not text:
            continue
        parsed = _parse_layer_item(text)
        if isinstance(parsed, list):
            for item in parsed:
                _append_layer(layers, item, raw=text)
        else:
            _append_layer(layers, parsed, raw=text)
    return layers


def _parse_layer_item(raw: str) -> Any:
    if _looks_like_json(raw):
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            Renderer.die(f"layer 不是合法 JSON: {raw!r}\n{exc}")
            raise SystemExit(1)
    return _parse_layer_mapping(raw)


def _parse_layer_mapping(raw: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if not parts:
        Renderer.die(
            "layer 不能为空。\n"
            "模板: --layer '{\"name\":\"base_system\",\"source\":\"user\",\"content\":\"...\"}'\n"
            "或: --layer 'name=base_system,source=user,content=...'",
        )
        raise SystemExit(1)
    for part in parts:
        if "=" in part:
            key, _, value = part.partition("=")
        elif ":" in part:
            key, _, value = part.partition(":")
        else:
            Renderer.die(
                "layer 必须是 JSON 对象，或 key=value / key:value 形式。\n"
                f"收到: {raw!r}",
            )
            raise SystemExit(1)
        key = key.strip()
        if not key:
            Renderer.die(f"layer key 不能为空: {raw!r}")
            raise SystemExit(1)
        result[key] = value.strip()
    return result


def _append_layer(target: list[dict[str, Any]], item: Any, *, raw: str) -> None:
    if not isinstance(item, dict):
        Renderer.die(f"layer 必须是对象或对象数组，不能是 {type(item).__name__}: {raw!r}")
        raise SystemExit(1)
    target.append(item)


def _looks_like_json(raw: str) -> bool:
    return (raw.startswith("{") and raw.endswith("}")) or (raw.startswith("[") and raw.endswith("]"))


def _layers_or_default(values: list[str] | None) -> list[dict[str, Any]]:
    parsed = _parse_layers(values)
    if parsed is None:
        return list(DEFAULT_BUNDLE_LAYERS)
    if not parsed:
        Renderer.die("至少提供一个 layer，或者干脆不要传 `--layer` 使用默认 layers。")
        raise SystemExit(1)
    return parsed


def _prompt_repo(session: Any) -> PromptRepo:
    return PromptRepo(session)


# -------------------- 查看 --------------------


@prompt_app.command("list", help="列出所有 prompt bundle")
def list_cmd() -> None:
    asyncio.run(_list())


async def _list() -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        bundles = await _prompt_repo(session).list_bundles()
    _render_bundle_rows(bundles)


@prompt_app.command("show", help="查看某个 bundle 的详情")
def show_cmd(
    name: Annotated[str, typer.Argument(help="bundle ?????????? bundle")] = "",
) -> None:
    asyncio.run(_versions(name or None))


async def _versions(name: str | None) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        repo = _prompt_repo(session)
        if name is None:
            _render_bundle_rows(await repo.list_bundles())
            Renderer.out("提示：使用 `chariot prompt versions <bundle>` 查看某个 bundle 的版本。")
            return
        bundle = await repo.get_bundle(name)
        if bundle is None:
            Renderer.die(f"未找到 prompt bundle: {name!r}")
            return
        versions = await repo.list_versions(name)

    if not versions:
        Renderer.out(f"(bundle {name!r} 没有版本)")
        return
    rows = [
        (
            entry.version,
            "yes" if entry.is_active else "no",
            str(len(entry.spec.get("layers", []))),
            _fmt_dt(entry.created_at),
            _fmt_dt(entry.updated_at),
        )
        for entry in versions
    ]
    Renderer.table(["version", "active", "layers", "created_at", "updated_at"], rows, title=f"versions of {bundle.name}")


@prompt_app.command("version", help="查看某个 bundle 的指定版本")
def version_cmd(
    bundle_name: Annotated[str, typer.Argument(help="bundle 名称")],
    version: Annotated[str, typer.Argument(help="版本号，例如 v1")],
) -> None:
    asyncio.run(_version(bundle_name, version))


async def _version(bundle_name: str, version: str) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        repo = _prompt_repo(session)
        entry = await repo.get_version(bundle_name, version)
        if entry is None:
            Renderer.die(f"未找到 prompt version: {bundle_name!r}:{version!r}")
            return

    Renderer.kv(
        {
            "bundle": entry.bundle_name,
            "version": entry.version,
            "active": "yes" if entry.is_active else "no",
            "created_at": _fmt_dt(entry.created_at),
            "updated_at": _fmt_dt(entry.updated_at),
        }
    )
    Renderer.out("")
    layers = entry.spec.get("layers", [])
    if isinstance(layers, list):
        _render_layers_table(layers)
    else:
        Renderer.out(_json_text(layers))


@prompt_app.command(
    "traces",
    help="列出 prompt trace；可按 bundle 过滤",
)
def traces_cmd(
    bundle: Annotated[str, typer.Option("--bundle", help="只看某个 bundle")] = "",
    limit: Annotated[int, typer.Option("--limit", help="返回条数上限")] = 50,
    offset: Annotated[int, typer.Option("--offset", help="跳过前 N 条")] = 0,
) -> None:
    asyncio.run(_traces(bundle or None, limit=limit, offset=offset))


async def _traces(bundle: str | None, *, limit: int, offset: int) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        repo = _prompt_repo(session)
        if bundle is None:
            entries = await repo.list_traces(limit=limit, offset=offset)
        else:
            entries = await repo.list_traces_by_bundle(bundle, limit=limit, offset=offset)
            if not entries:
                found = await repo.get_bundle(bundle)
                if found is None:
                    Renderer.die(f"未找到 prompt bundle: {bundle!r}")
                    return
    _render_trace_rows(entries)


@prompt_app.command("inspect", help="查看单条 prompt trace 的详情")
def inspect_cmd(
    trace_id: Annotated[str, typer.Argument(help="trace id")],
) -> None:
    asyncio.run(_inspect(trace_id))


async def _inspect(trace_id: str) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        trace = await _prompt_repo(session).get_trace(trace_id)
        if trace is None:
            Renderer.die(f"未找到 prompt trace: {trace_id!r}")
            return

    Renderer.kv(
        {
            "trace_id": trace.id,
            "bundle": trace.bundle_name,
            "version": trace.version,
            "conversation_id": trace.conversation_id or "-",
            "provider": trace.provider_name,
            "model": trace.model or "-",
            "prompt_size": trace.prompt_size,
            "created_at": _fmt_dt(trace.created_at),
        }
    )
    Renderer.out("")
    Renderer.out("request:")
    Renderer.out(_json_text(trace.request))
    Renderer.out("")
    Renderer.out("source_refs:")
    source_rows = [
        (ref.get("layer", "-"), ref.get("source", "-"), "yes" if ref.get("present") else "no")
        for ref in trace.source_refs
    ]
    Renderer.table(["layer", "source", "present"], source_rows, title="source refs")


# -------------------- 管理 --------------------


@prompt_app.command("add", help="创建 prompt bundle；不传 --layer 时使用默认 layers")
def add_cmd(
    name: Annotated[str, typer.Argument(help="bundle 名称")],
    description: Annotated[
        str,
        typer.Option("--description", "-d", help="bundle 说明"),
    ] = "",
    layers: Annotated[
        list[str],
        typer.Option(
            "--layer",
            "-l",
            help="单个 layer；支持 JSON 对象或 key=value,key=value 形式，可重复传入；不传时使用默认 layers",
        ),
    ] = [],
) -> None:
    asyncio.run(_add(name, description=description, layers=layers))


async def _add(name: str, *, description: str, layers: list[str]) -> None:
    bundle_layers = list(DEFAULT_BUNDLE_LAYERS) if not layers else _layers_or_default(layers)
    bundle_description = description or None
    async with installed_runtime() as agent:
        try:
            async with agent.session_maker() as session:
                entry = await _prompt_repo(session).create_bundle(
                    name,
                    description=bundle_description,
                    layers=bundle_layers,
                )
        except ConfigError as exc:
            Renderer.die(f"创建失败: {exc}")
            return
    Renderer.out(f"+ {entry.bundle_name}:{entry.version}")


@prompt_app.command("update", help="更新 bundle 的说明或 layers，并生成新版本；空层表示不改 layers")
def update_cmd(
    name: Annotated[str, typer.Argument(help="bundle 名称")],
    description: Annotated[
        str,
        typer.Option("--description", "-d", help="新的 bundle 说明"),
    ] = _MISSING,
    layers: Annotated[
        list[str],
        typer.Option(
            "--layer",
            "-l",
            help="单个 layer；支持 JSON 对象或 key=value,key=value 形式，可重复传入；空层表示不改 layers",
        ),
    ] = [],
) -> None:
    asyncio.run(_update(name, description=description, layers=layers))


async def _update(name: str, *, description: str | object, layers: list[str]) -> None:
    if description is _MISSING and not layers:
        Renderer.die("至少提供 `--description` 或 `--layer` 之一。")
        return
    parsed_layers = _parse_layers(layers) if layers else None
    kwargs: dict[str, Any] = {}
    if description is not _MISSING:
        kwargs["description"] = description or None
    if parsed_layers is not None:
        kwargs["layers"] = parsed_layers
    async with installed_runtime() as agent:
        try:
            async with agent.session_maker() as session:
                entry = await _prompt_repo(session).update_bundle(name, **kwargs)
        except ConfigError as exc:
            Renderer.die(f"更新失败: {exc}")
            return
    Renderer.out(f"~ {entry.bundle_name}:{entry.version}")


@prompt_app.command("activate", help="激活 bundle 或某个版本")
def activate_cmd(
    name: Annotated[str, typer.Argument(help="bundle 名称")],
    version: Annotated[str, typer.Argument(help="版本号；省略则只激活 bundle")] = "",
) -> None:
    asyncio.run(_activate(name, version or None))


async def _activate(name: str, version: str | None) -> None:
    async with installed_runtime() as agent:
        try:
            async with agent.session_maker() as session:
                repo = _prompt_repo(session)
                if version is None:
                    entry = await repo.activate_bundle(name)
                    Renderer.out(f"* {entry.name} 已激活")
                    return
                entry = await repo.activate_version(name, version)
        except ConfigError as exc:
            Renderer.die(f"激活失败: {exc}")
            return
    Renderer.out(f"* {entry.bundle_name}:{entry.version} 已激活")


def register(app: typer.Typer) -> None:
    app.add_typer(prompt_app)

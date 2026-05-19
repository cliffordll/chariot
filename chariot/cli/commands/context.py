"""Context bundle and snapshot commands."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

import typer

from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.services.context import ContextService

context_app = typer.Typer(
    name="context",
    help="管理 context bundles、versions 与 runtime snapshots",
    no_args_is_help=True,
)


def _fmt_dt(value: Any) -> str:
    if value is None:
        return "-"
    if hasattr(value, "isoformat"):
        return value.isoformat(timespec="seconds")
    return str(value)


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _parse_spec(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        Renderer.die(f"spec must be valid JSON: {exc}")
        raise SystemExit(1) from exc
    if not isinstance(value, dict):
        Renderer.die("spec must be a JSON object")
        raise SystemExit(1) from None
    return value


@context_app.command("list", help="列出 context bundles")
def list_cmd() -> None:
    asyncio.run(_list_bundles())


@context_app.command("show", help="查看单个 context bundle 与其 versions")
def show_cmd(
    name: Annotated[str, typer.Argument(help="bundle name or id")],
) -> None:
    asyncio.run(_show_bundle(name))


@context_app.command("add", help="创建 context bundle")
def add_cmd(
    name: Annotated[str, typer.Option("--name", help="bundle name")] = "",
    description: Annotated[str, typer.Option("--description", help="bundle description")] = "",
    spec: Annotated[str, typer.Option("--spec", help="JSON spec")] = "",
) -> None:
    asyncio.run(_add_bundle(name=name, description=description, spec=spec))


@context_app.command("update", help="更新 context bundle 并生成新版本")
def update_cmd(
    name: Annotated[str, typer.Argument(help="bundle name or id")],
    rename: Annotated[str | None, typer.Option("--rename", help="new bundle name")] = None,
    description: Annotated[str | None, typer.Option("--description", help="new description")] = None,
    spec: Annotated[str, typer.Option("--spec", help="JSON spec")] = "",
) -> None:
    asyncio.run(_update_bundle(name=name, rename=rename, description=description, spec=spec))


@context_app.command("activate", help="激活 bundle 或指定 version")
def activate_cmd(
    name: Annotated[str, typer.Argument(help="bundle name or id")],
    version: Annotated[str, typer.Option("--version", help="specific version to activate")] = "",
) -> None:
    asyncio.run(_activate_bundle(name=name, version=version or None))


@context_app.command("snapshots", help="列出 context snapshots")
def snapshots_cmd(
    conversation: Annotated[str, typer.Option("--conversation", help="按 conversation id 过滤")] = "",
    limit: Annotated[int, typer.Option("--limit", help="最多列出多少条")] = 50,
    offset: Annotated[int, typer.Option("--offset", help="偏移量")] = 0,
) -> None:
    asyncio.run(_snapshots(conversation or None, limit=limit, offset=offset))


@context_app.command("traces", help="列出 context traces")
def traces_cmd(
    conversation: Annotated[str, typer.Option("--conversation", help="按 conversation id 过滤")] = "",
    limit: Annotated[int, typer.Option("--limit", help="最多列出多少条")] = 50,
    offset: Annotated[int, typer.Option("--offset", help="偏移量")] = 0,
) -> None:
    asyncio.run(_traces(conversation or None, limit=limit, offset=offset))


@context_app.command("inspect", help="查看单条 context snapshot/trace 详情")
def inspect_cmd(
    context_id: Annotated[str, typer.Argument(help="snapshot id 或 trace id")],
) -> None:
    asyncio.run(_inspect(context_id))


async def _list_bundles() -> None:
    async with installed_runtime() as agent:
        entries = await ContextService(agent).list_bundles()
    if not entries:
        Renderer.out("(没有 context bundles)")
        return
    rows = [
        (
            entry.id,
            entry.name,
            "yes" if entry.is_active else "no",
            entry.active_version or "-",
            str(entry.version_count),
            entry.description or "-",
        )
        for entry in entries
    ]
    Renderer.table(["id", "name", "active", "version", "count", "description"], rows, title="context bundles")


async def _show_bundle(name: str) -> None:
    async with installed_runtime() as agent:
        service = ContextService(agent)
        bundle = await service.get_bundle(name)
        if bundle is None:
            Renderer.die(f"context bundle not found: {name!r}")
            return
        versions = await service.list_versions(name)
    Renderer.kv(
        {
            "id": bundle.id,
            "name": bundle.name,
            "active": "yes" if bundle.is_active else "no",
            "active_version": bundle.active_version or "-",
            "version_count": str(bundle.version_count),
            "description": bundle.description or "-",
            "created_at": _fmt_dt(bundle.created_at),
            "updated_at": _fmt_dt(bundle.updated_at),
        }
    )
    Renderer.out("")
    if not versions:
        Renderer.out("(no versions)")
        return
    rows = [
        (
            entry.id,
            entry.version,
            "yes" if entry.is_active else "no",
            _fmt_dt(entry.updated_at),
        )
        for entry in versions
    ]
    Renderer.table(["id", "version", "active", "updated_at"], rows, title="context versions")
    Renderer.out("")
    for entry in versions:
        Renderer.out(f"{entry.bundle_name}:{entry.version}")
        Renderer.out(_json_text(entry.spec))


async def _add_bundle(*, name: str, description: str, spec: str) -> None:
    if not name.strip():
        Renderer.die("--name is required")
        return
    parsed_spec = _parse_spec(spec) or {}
    async with installed_runtime() as agent:
        version = await ContextService(agent).create_bundle(
            name.strip(),
            description=description.strip() or None,
            spec=parsed_spec,
        )
    Renderer.out(f"+ {version.bundle_name}:{version.version}")


async def _update_bundle(*, name: str, rename: str | None, description: str | None, spec: str) -> None:
    parsed_spec = _parse_spec(spec)
    async with installed_runtime() as agent:
        service = ContextService(agent)
        target = name
        if rename is not None and rename.strip() and rename.strip() != name:
            bundle = await service.rename_bundle(name, rename.strip())
            target = bundle.id
        version = await service.update_bundle(
            target,
            description=description,
            spec=parsed_spec,
        )
    Renderer.out(f"~ {version.bundle_name}:{version.version}")


async def _activate_bundle(*, name: str, version: str | None) -> None:
    async with installed_runtime() as agent:
        service = ContextService(agent)
        if version is None:
            bundle = await service.activate_bundle(name)
            Renderer.out(f"* active bundle: {bundle.name}")
            return
        entry = await service.activate_version(name, version)
        Renderer.out(f"* active version: {entry.bundle_name}:{entry.version}")


async def _snapshots(conversation_id: str | None, *, limit: int, offset: int) -> None:
    async with installed_runtime() as agent:
        entries = await ContextService(agent).list_snapshots(
            conversation_id=conversation_id,
            limit=limit,
            offset=offset,
        )
    if not entries:
        Renderer.out("(没有 context snapshots)")
        return
    rows = [
        (
            entry.id,
            entry.conversation_id or "-",
            entry.provider_snapshot,
            entry.model or "-",
            str(entry.context_size),
            _fmt_dt(entry.created_at),
        )
        for entry in entries
    ]
    Renderer.table(
        ["id", "conversation", "provider", "model", "size", "created_at"],
        rows,
        title="context snapshots",
    )


async def _traces(conversation_id: str | None, *, limit: int, offset: int) -> None:
    async with installed_runtime() as agent:
        entries = await ContextService(agent).list_traces(
            conversation_id=conversation_id,
            limit=limit,
            offset=offset,
        )
    if not entries:
        Renderer.out("(没有 context traces)")
        return
    rows = [
        (
            entry.id,
            entry.snapshot_id,
            entry.bundle_name or entry.bundle_id or "-",
            entry.version or "-",
            entry.conversation_id or "-",
            entry.provider_snapshot,
            entry.model or "-",
            entry.prompt_trace_id or "-",
            _fmt_dt(entry.created_at),
        )
        for entry in entries
    ]
    Renderer.table(
        ["trace", "snapshot", "bundle", "version", "conversation", "provider", "model", "prompt trace", "created_at"],
        rows,
        title="context traces",
    )


async def _inspect(context_id: str) -> None:
    async with installed_runtime() as agent:
        entry = await ContextService(agent).inspect_context(context_id)
    if entry is None:
        Renderer.die(f"未找到 context: {context_id!r}")
        return
    snapshot = entry["snapshot"]
    trace = entry["trace"]
    Renderer.kv(
        {
            "snapshot": snapshot["id"] if snapshot is not None else "-",
            "trace": trace["id"] if trace is not None else "-",
            "bundle": trace["bundle_name"] if trace is not None else "-",
            "version": trace["version"] if trace is not None else "-",
            "conversation": snapshot["conversation_id"]
            if snapshot is not None
            else (trace["conversation_id"] if trace is not None else "-"),
            "provider": snapshot["provider_snapshot"]
            if snapshot is not None
            else (trace["provider_snapshot"] if trace is not None else "-"),
            "model": snapshot["model"] if snapshot is not None else (trace["model"] if trace is not None else "-"),
            "size": snapshot["context_size"] if snapshot is not None else "-",
        }
    )
    if snapshot is not None:
        Renderer.out("snapshot.request")
        Renderer.out(_json_text(snapshot["request"]))
        Renderer.out("snapshot.slices")
        Renderer.out(_json_text(snapshot["slices"]))
    if trace is not None:
        Renderer.out("trace.policy")
        Renderer.out(_json_text(trace["policy"]))
        Renderer.out("trace.selected_refs")
        Renderer.out(_json_text(trace["selected_refs"]))


def register(app: typer.Typer) -> None:
    app.add_typer(context_app)

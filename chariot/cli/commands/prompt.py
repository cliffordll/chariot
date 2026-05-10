"""`chariot prompt` command group."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

import typer

from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.repos.prompt_repo import PromptRepo

prompt_app = typer.Typer(
    name="prompt",
    help="查看 prompt bundle / version / trace",
    no_args_is_help=True,
)


@prompt_app.command("list", help="列出 prompt bundles")
def list_cmd() -> None:
    asyncio.run(_list())


async def _list() -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        bundles = await PromptRepo(session).list_bundles()
    if not bundles:
        Renderer.out("(没有 prompt bundles)")
        return
    rows = [(b.id, b.name, str(b.version_count), b.description or "-") for b in bundles]
    Renderer.table(["id", "name", "versions", "description"], rows, title="prompt bundles")


@prompt_app.command("show", help="显示一个 prompt bundle 的 layers 和 versions")
def show_cmd(
    name: Annotated[str, typer.Argument(help="bundle name")],
) -> None:
    asyncio.run(_show(name))


async def _show(name: str) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        repo = PromptRepo(session)
        bundle = await repo.get_bundle(name)
        versions = await repo.list_versions(name)
    if bundle is None:
        Renderer.die(f"未找到 prompt bundle: {name!r}")
        return
    Renderer.out(f"id:           {bundle.id}")
    Renderer.out(f"name:         {bundle.name}")
    Renderer.out(f"description:  {bundle.description or '-'}")
    Renderer.out(f"version_count: {bundle.version_count}")
    Renderer.out("")
    Renderer.out("layers:")
    for layer in bundle.layers:
        Renderer.out(f"- {layer.get('name')}: {layer.get('source')}")
    Renderer.out("")
    if versions:
        rows = [(v.id, v.version, json.dumps(v.spec, ensure_ascii=False)) for v in versions]
        Renderer.table(["id", "version", "spec"], rows, title="versions")


@prompt_app.command("versions", help="列出一个 prompt bundle 的 versions")
def versions_cmd(
    name: Annotated[str | None, typer.Argument(help="bundle name")] = None,
) -> None:
    asyncio.run(_versions(name))


async def _versions(name: str | None) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        repo = PromptRepo(session)
        if name is None:
            bundles = await repo.list_bundles()
            if not bundles:
                Renderer.out("(没有 prompt bundles)")
                return
            rows = [(b.id, b.name, str(b.version_count), b.description or "-") for b in bundles]
            Renderer.table(["id", "name", "versions", "description"], rows, title="prompt bundles")
            Renderer.out("")
            Renderer.out("use `chariot prompt versions <bundle>` to inspect a bundle")
            return
        bundle = await repo.get_bundle(name)
        versions = await repo.list_versions(name)
    if bundle is None:
        Renderer.die(f"未找到 prompt bundle: {name!r}")
        return
    if not versions:
        Renderer.out(f"(bundle {name!r} 没有 versions)")
        return
    rows = [
        (v.id, v.version, v.created_at.isoformat(), json.dumps(v.spec, ensure_ascii=False))
        for v in versions
    ]
    Renderer.table(["id", "version", "created_at", "spec"], rows, title=f"versions for {name}")


@prompt_app.command("version", help="显示一个 prompt version")
def version_cmd(
    name: Annotated[str, typer.Argument(help="bundle name")],
    version: Annotated[str, typer.Argument(help="version name")],
) -> None:
    asyncio.run(_version(name, version))


async def _version(name: str, version: str) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        entry = await PromptRepo(session).get_version(name, version)
    if entry is None:
        Renderer.die(f"未找到 prompt version: {name!r}:{version!r}")
        return
    Renderer.out(f"id:           {entry.id}")
    Renderer.out(f"bundle_id:    {entry.bundle_id}")
    Renderer.out(f"bundle_name:  {entry.bundle_name}")
    Renderer.out(f"version:      {entry.version}")
    Renderer.out(f"created_at:   {entry.created_at.isoformat()}")
    Renderer.out(f"updated_at:   {entry.updated_at.isoformat()}")
    Renderer.out("")
    Renderer.out(json.dumps(entry.spec, ensure_ascii=False, indent=2))


@prompt_app.command("inspect", help="查看一条 prompt trace")
def inspect_cmd(
    trace_id: Annotated[str, typer.Argument(help="prompt trace id")],
) -> None:
    asyncio.run(_inspect(trace_id))


async def _inspect(trace_id: str) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        trace = await PromptRepo(session).get_trace(trace_id)
    if trace is None:
        Renderer.die(f"未找到 prompt trace: {trace_id!r}")
        return
    Renderer.out(f"id:             {trace.id}")
    Renderer.out(f"bundle:         {trace.bundle_name}")
    Renderer.out(f"version:        {trace.version}")
    Renderer.out(f"conversation_id: {trace.conversation_id or '-'}")
    Renderer.out(f"provider_name:  {trace.provider_name}")
    Renderer.out(f"model:          {trace.model or '-'}")
    Renderer.out(f"prompt_size:    {trace.prompt_size}")
    Renderer.out(f"created_at:     {trace.created_at.isoformat()}")
    Renderer.out("")
    Renderer.out("source_refs:")
    Renderer.out(json.dumps(trace.source_refs, ensure_ascii=False, indent=2))
    Renderer.out("")
    Renderer.out("request:")
    Renderer.out(json.dumps(trace.request, ensure_ascii=False, indent=2))


@prompt_app.command("traces", help="列出最近 prompt traces")
def traces_cmd(
    bundle_name: Annotated[str | None, typer.Option("--bundle", help="bundle name")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=200, help="max rows")] = 50,
    offset: Annotated[int, typer.Option("--offset", min=0, help="row offset")] = 0,
) -> None:
    asyncio.run(_traces(bundle_name=bundle_name, limit=limit, offset=offset))


async def _traces(*, bundle_name: str | None, limit: int, offset: int) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        repo = PromptRepo(session)
        if bundle_name is None:
            entries = await repo.list_traces(limit=limit, offset=offset)
        else:
            entries = await repo.list_traces_by_bundle(bundle_name, limit=limit, offset=offset)
    if not entries:
        Renderer.out("(没有 prompt traces)")
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
        )
        for entry in entries
    ]
    Renderer.table(
        ["id", "bundle", "version", "conversation", "provider", "model", "size"],
        rows,
        title="prompt traces",
    )


def register(app: typer.Typer) -> None:
    app.add_typer(prompt_app)

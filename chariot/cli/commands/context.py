"""Context management commands."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

import typer

from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.repos.context_repo import ContextRepo

context_app = typer.Typer(
    name="context",
    help="查看 context snapshots 和 traces",
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


@context_app.command("list", help="列出 context snapshots")
def list_cmd() -> None:
    asyncio.run(_list())


@context_app.command("traces", help="列出 context traces")
def traces_cmd(
    conversation: Annotated[
        str,
        typer.Option("--conversation", help="按 conversation id 过滤；留空表示全部"),
    ] = "",
    limit: Annotated[int, typer.Option("--limit", help="最多列出多少条")] = 50,
    offset: Annotated[int, typer.Option("--offset", help="偏移量")] = 0,
) -> None:
    asyncio.run(_traces(conversation or None, limit=limit, offset=offset))


@context_app.command("inspect", help="查看单条 context 的详情")
def inspect_cmd(
    context_id: Annotated[str, typer.Argument(help="snapshot id 或 trace id")],
) -> None:
    asyncio.run(_inspect(context_id))


async def _list() -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        entries = await ContextRepo(session).list_snapshots()
    if not entries:
        Renderer.out("(没有 context snapshots)")
        return
    rows = [
        (
            entry.id,
            entry.conversation_id or "-",
            entry.provider_name,
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
    async with installed_runtime() as agent, agent.session_maker() as session:
        entries = await ContextRepo(session).list_traces(
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
            entry.conversation_id or "-",
            entry.provider_name,
            entry.model or "-",
            entry.prompt_trace_id or "-",
            _fmt_dt(entry.created_at),
        )
        for entry in entries
    ]
    Renderer.table(
        ["trace", "snapshot", "conversation", "provider", "model", "prompt trace", "created_at"],
        rows,
        title="context traces",
    )


async def _inspect(context_id: str) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        entry = await ContextRepo(session).inspect_context(context_id)
    if entry is None:
        Renderer.die(f"未找到 context: {context_id!r}")
        return
    snapshot = entry["snapshot"]
    trace = entry["trace"]
    Renderer.kv(
        {
            "snapshot": snapshot["id"] if snapshot is not None else "-",
            "trace": trace["id"] if trace is not None else "-",
            "conversation": snapshot["conversation_id"]
            if snapshot is not None
            else (trace["conversation_id"] if trace is not None else "-"),
            "provider": snapshot["provider_name"]
            if snapshot is not None
            else (trace["provider_name"] if trace is not None else "-"),
            "model": snapshot["model"]
            if snapshot is not None
            else (trace["model"] if trace is not None else "-"),
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

"""`chariot audit <subcmd>` —— audit_events 浏览(B5 wave 2)。

子命令:
- `chariot audit list [--limit N]`:列最近 N 条 audit 事件
- `chariot audit show <event_id>`:看单条 event 的 payload 详情
- `chariot audit tail [--limit N]`:`list` 别名,语义更明确
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

import typer

from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.repos.audit_repo import AuditEvent
from chariot.services.audit import AuditService

audit_app = typer.Typer(
    name="audit",
    help="审计事件浏览(B5 wave 2)",
    no_args_is_help=True,
)


@audit_app.command("list", help="列最近的审计事件")
def list_cmd(
    limit: Annotated[int, typer.Option("--limit", "-n", help="返回条数")] = 50,
) -> None:
    asyncio.run(_list(limit))


@audit_app.command("tail", help="`list` 别名 —— 列最近的审计事件")
def tail_cmd(
    limit: Annotated[int, typer.Option("--limit", "-n", help="返回条数")] = 50,
) -> None:
    asyncio.run(_list(limit))


@audit_app.command("show", help="查看单条 audit event 详情")
def show_cmd(
    event_id: Annotated[str, typer.Argument(help="audit event id")],
) -> None:
    asyncio.run(_show(event_id))


async def _list(limit: int) -> None:
    async with installed_runtime() as agent:
        events = await AuditService(agent).list_events(limit=limit)
    if not events:
        Renderer.out("(没有 audit events)")
        return
    rows = [
        (
            event.id,
            event.event_type,
            event.status or "-",
            _summary(event),
            _fmt_dt(event.created_at),
        )
        for event in events
    ]
    Renderer.table(["id", "event", "status", "summary", "at"], rows, title="audit events")


async def _show(event_id: str) -> None:
    async with installed_runtime() as agent:
        event = await AuditService(agent).get_event(event_id)
    if event is None:
        Renderer.die(f"audit event not found: {event_id!r}")
        return
    Renderer.kv(
        {
            "id": event.id,
            "event_type": event.event_type,
            "status": event.status or "-",
            "created_at": _fmt_dt(event.created_at),
        }
    )
    Renderer.out("")
    Renderer.out("payload:")
    Renderer.out(json.dumps(event.payload, ensure_ascii=False, indent=2, sort_keys=True))


def _summary(event: AuditEvent) -> str:
    """根据 event_type 抽 payload 关键字段做单行摘要,避免列里灌大 JSON。"""
    payload = event.payload
    et = event.event_type
    if et in {"tool_call_pre", "tool_call_post"}:
        tool = payload.get("tool_name", "?")
        if et == "tool_call_post":
            dur = payload.get("duration_ms")
            return f"{tool} ({dur}ms)" if dur is not None else tool
        return str(tool)
    if et == "guardrail_verdict":
        return f"{payload.get('rule_id', '?')} → {payload.get('verdict', '?')}"
    if et == "memory_store":
        return f"{payload.get('action', '?')} {payload.get('memory_id', '?')}"
    if et == "checkpoint_create":
        return f"{payload.get('kind', '?')} {payload.get('name', '?')}"
    if et == "rollback":
        return f"{payload.get('checkpoint_id', '?')} ok={payload.get('ok')}"
    return ""


def _fmt_dt(value: object) -> str:
    if value is None:
        return "-"
    if hasattr(value, "isoformat"):
        return value.isoformat(timespec="seconds")  # type: ignore[attr-defined]
    return str(value)


def register(app: typer.Typer) -> None:
    app.add_typer(audit_app)

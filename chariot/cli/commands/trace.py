"""`chariot trace` commands(Phase B1)。

Trace 是后续 reflection / evaluation / curator / RL 的数据底座。CLI 提供:

- `chariot trace list`:列最近 turn(过滤:conversation / task / provider / status)
- `chariot trace show <turn-id>`:看单个 turn 元数据
- `chariot trace view <turn-id>`:按执行时间线展开 turn → model call(s) + tool call(s) + checkpoint(s)
- `chariot trace reconcile`:把 stale running turn 标 cancelled(进程崩溃后清理)
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

import typer

from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.models.trace import TurnStatus
from chariot.services.prompt import PromptService
from chariot.services.trace import TraceService

trace_app = typer.Typer(name="trace", help="Inspect trace turns (Phase B1)", no_args_is_help=True)


def _prompt_repo(runtime: Any) -> PromptService:
    return PromptService(runtime)


def _fmt_dt(value: Any) -> str:
    if value is None:
        return "-"
    if hasattr(value, "isoformat"):
        return value.isoformat(timespec="seconds")
    return str(value)


def _fmt_dur(ms: int | None) -> str:
    if ms is None:
        return "-"
    if ms < 1000:
        return f"{ms}ms"
    return f"{ms / 1000:.2f}s"


def _parse_status(value: str) -> TurnStatus | None:
    if not value:
        return None
    try:
        return TurnStatus(value)
    except ValueError:
        Renderer.die(f"invalid status: {value!r}; expected one of {[s.value for s in TurnStatus]}")
        raise SystemExit(1) from None


@trace_app.command("list", help="List recent trace turns")
def trace_list_cmd(
    conversation: Annotated[str, typer.Option("--conversation", help="filter by conversation_id")] = "",
    task: Annotated[str, typer.Option("--task", help="filter by task_id")] = "",
    provider: Annotated[str, typer.Option("--provider", help="filter by provider_snapshot")] = "",
    status: Annotated[str, typer.Option("--status", help="running / completed / failed / cancelled")] = "",
    limit: Annotated[int, typer.Option("--limit", help="max rows")] = 50,
    offset: Annotated[int, typer.Option("--offset", help="page offset")] = 0,
) -> None:
    asyncio.run(
        _trace_list(
            conversation=conversation or None,
            task=task or None,
            provider=provider or None,
            status=_parse_status(status),
            limit=limit,
            offset=offset,
        )
    )


@trace_app.command("show", help="Show one trace turn metadata")
def trace_show_cmd(
    turn_id: Annotated[str, typer.Argument(help="trace turn id")],
) -> None:
    asyncio.run(_trace_show(turn_id))


@trace_app.command("view", help="View a trace turn as an execution timeline (model calls + tool calls + checkpoints)")
def trace_view_cmd(
    turn_id: Annotated[str, typer.Argument(help="trace turn id")],
) -> None:
    asyncio.run(_trace_view(turn_id))


@trace_app.command("reconcile", help="Mark stale running turns as cancelled (after crashes)")
def trace_reconcile_cmd(
    older_than: Annotated[int, typer.Option("--older-than", help="seconds threshold")] = 3600,
) -> None:
    asyncio.run(_trace_reconcile(older_than))


async def _trace_list(
    *,
    conversation: str | None,
    task: str | None,
    provider: str | None,
    status: TurnStatus | None,
    limit: int,
    offset: int,
) -> None:
    async with installed_runtime() as agent:
        turns = await TraceService(agent).list_turns(
            conversation_id=conversation,
            task_id=task,
            provider_snapshot=provider,
            status=status,
            limit=limit,
            offset=offset,
        )
    if not turns:
        Renderer.out("(没有 trace turn)")
        return
    rows = [
        (
            t.status.value,
            t.stop_reason or "-",
            f"M{t.provider_calls_count}/T{t.tool_calls_count}",
            t.conversation_id or "-",
            t.id,
            t.task_id or "-",
            _fmt_dt(t.started_at),
        )
        for t in turns
    ]
    Renderer.table(
        ["status", "reason", "calls", "conversation", "turn_id", "task", "started_at"],
        rows,
        title="trace turns",
    )


async def _trace_show(turn_id: str) -> None:
    async with installed_runtime() as agent:
        turn = await TraceService(agent).get_turn(turn_id)
    if turn is None:
        Renderer.die(f"trace turn not found: {turn_id!r}")
        return
    Renderer.kv(
        {
            "id": turn.id,
            "status": turn.status.value,
            "provider": turn.provider_snapshot,
            "model": turn.model or "-",
            "conversation_id": turn.conversation_id or "-",
            "agent_profile": turn.agent_profile or "-",
            "task_id": turn.task_id or "-",
            "task_run_id": turn.task_run_id or "-",
            "provider_calls": turn.provider_calls_count,
            "tool_calls": turn.tool_calls_count,
            "stop_reason": turn.stop_reason or "-",
            "error_type": turn.error_type or "-",
            "error_message": turn.error_message or "-",
            "input_tokens": turn.input_tokens,
            "output_tokens": turn.output_tokens,
            "cache_read_tokens": turn.cache_read_tokens,
            "cache_write_tokens": turn.cache_write_tokens,
            "reasoning_tokens": turn.reasoning_tokens,
            "cost_usd": turn.cost_usd,
            "cost_status": turn.cost_status or "-",
            "duration": _fmt_dur(turn.duration_ms),
            "started_at": _fmt_dt(turn.started_at),
            "finished_at": _fmt_dt(turn.finished_at),
        }
    )
    if turn.meta:
        Renderer.out("")
        Renderer.out("meta:")
        Renderer.out(json.dumps(turn.meta, ensure_ascii=False, indent=2, sort_keys=True))


async def _trace_view(turn_id: str) -> None:
    async with installed_runtime() as agent:
        trace_service = TraceService(agent)
        tree = await trace_service.get_tree(turn_id)
        turn_ordinal = await trace_service.get_turn_ordinal(turn_id)
        prompt_repo = _prompt_repo(agent)
    if tree is None:
        Renderer.die(f"trace turn not found: {turn_id!r}")
        return

    turn = tree.turn
    round_text = "-"
    if turn_ordinal is not None:
        round_text = f"{turn_ordinal[0]}/{turn_ordinal[1]}"
    Renderer.out(
        f"▼ turn id: {turn.id}  [{turn.status.value}]  "
        f"{turn.provider_snapshot}{('/' + turn.model) if turn.model else ''}"
    )
    Renderer.out(f"  conversation: {turn.conversation_id or '-'}")
    Renderer.out(f"  round: {round_text}")
    Renderer.out(f"  reason: {turn.stop_reason or '-'}")
    Renderer.out(
        f"  calls: M{turn.provider_calls_count}/T{turn.tool_calls_count}  "
        f"duration={_fmt_dur(turn.duration_ms)}  "
        f"in/out={turn.input_tokens}/{turn.output_tokens}  "
        f"cost=${turn.cost_usd if turn.cost_usd is not None else '-'}"
    )
    if turn.error_type:
        Renderer.out(f"  error: {turn.error_type} {turn.error_message or ''}")

    Renderer.out("")
    Renderer.out("turn prompt trace:")
    if turn.prompt_trace_id:
        prompt_trace = await prompt_repo.get_trace(turn.prompt_trace_id)
        if prompt_trace is not None:
            Renderer.out(
                f"  prompt trace id: {prompt_trace.id}  "
                f"{prompt_trace.bundle_name}:{prompt_trace.version} / "
                f"{prompt_trace.provider_snapshot}"
                f"{('/' + prompt_trace.model) if prompt_trace.model else ''} / "
                f"size={prompt_trace.prompt_size}"
            )
        else:
            Renderer.out(f"  prompt trace id: {turn.prompt_trace_id}")
    else:
        Renderer.out("  prompt trace id: unavailable")
    Renderer.out(f"  context trace id: {turn.context_trace_id or 'unavailable'}")

    if tree.execution_groups:
        Renderer.out("")
        Renderer.out("execution timeline:")
        for group in tree.execution_groups:
            Renderer.out(f"  #{group.index}")
            if group.provider_call is not None:
                pc = group.provider_call
                err = f"  error={pc.error_type}" if pc.error_type else ""
                pc_model = f"/{pc.model}" if pc.model else ""
                Renderer.out(
                    f"    model: provider_call_id: {pc.id}  "
                    f"{pc.provider_snapshot}{pc_model}  latency={_fmt_dur(pc.latency_ms)}{err}"
                )
                if pc.response_summary:
                    Renderer.out(f"      response: {json.dumps(pc.response_summary, ensure_ascii=False)}")
            for tc in group.tool_calls:
                err = f"  error={tc.error_message}" if tc.error_message else ""
                Renderer.out(
                    f"    tool: tool_call_id: {tc.id}  "
                    f"{tc.tool_name}  [{tc.status.value}]  duration={_fmt_dur(tc.duration_ms)}{err}"
                )
                if tc.arguments:
                    Renderer.out(f"      args: {json.dumps(tc.arguments, ensure_ascii=False)}")
                if tc.result_summary:
                    Renderer.out(f"      result: {json.dumps(tc.result_summary, ensure_ascii=False)}")
    else:
        Renderer.out("")
        Renderer.out("execution timeline:")
        Renderer.out("  (empty)")

    if tree.checkpoints:
        Renderer.out("")
        Renderer.out("checkpoints:")
        for cp in tree.checkpoints:
            snap = f"  snapshot={cp.snapshot_id}" if cp.snapshot_id else ""
            Renderer.out(f"  - checkpoint id: {cp.id}  kind={cp.kind.value}{snap}  at={_fmt_dt(cp.created_at)}")
    else:
        Renderer.out("")
        Renderer.out("checkpoints:")
        Renderer.out("  (empty)")


async def _trace_reconcile(older_than_seconds: int) -> None:
    async with installed_runtime() as agent:
        cleaned = await TraceService(agent).reconcile_stale(older_than_seconds=older_than_seconds)
    Renderer.out(f"reconciled {cleaned} stale running turn(s)")


def register(app: typer.Typer) -> None:
    app.add_typer(trace_app)

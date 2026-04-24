"""`chariot logs` — 最近请求流水 + 实时 follow。

两种模式:
- 默认:按 `--limit` 拉最近 N 条(时间降序),打表格退出
- `--follow / -f`:先拉一批 tail(时间升序打),之后 polling(1s 间隔)增量追加;
  Ctrl+C 退出

polling 用 `list_logs(since=<last_created_at>)` 游标拉取,server 端已做 `>` 过滤,
不会重复返回本地已见过的记录。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated

import typer

from chariot.cli.core.render import Renderer
from chariot.sdk.client import ProxyClient
from chariot.server.controller.logs import LogOut

_POLL_INTERVAL_SEC = 1.0
_POLL_BATCH_LIMIT = 200


def logs_cmd(
    n: Annotated[int, typer.Option("-n", "--limit", help="最多显示多少条")] = 50,
    follow: Annotated[
        bool, typer.Option("-f", "--follow", help="持续跟踪新日志(Ctrl+C 退出)")
    ] = False,
) -> None:
    """显示请求日志;默认表格打印 N 条,--follow 持续追加增量。"""
    try:
        asyncio.run(_run(n=n, follow=follow))
    except KeyboardInterrupt:
        Renderer.stream_newline()


async def _run(*, n: int, follow: bool) -> None:
    try:
        async with ProxyClient.discover_session(spawn_if_missing=False) as client:
            if not follow:
                items = await client.list_logs(limit=n)
                _print_batch(items, header=True)
                return
            await _follow_loop(client, tail=n)
    except RuntimeError as e:
        Renderer.die(f"server 未就绪: {e}")


async def _follow_loop(client: ProxyClient, *, tail: int) -> None:
    """先打 tail 批,再无限 polling since=last_created_at。"""
    initial = await client.list_logs(limit=tail)
    # server 返回时间降序;follow 语义希望时间升序(新日志追加在下面)
    initial_asc = list(reversed(initial))
    _print_batch(initial_asc, header=True, follow=True)

    last_seen = initial_asc[-1].created_at if initial_asc else None
    while True:
        await asyncio.sleep(_POLL_INTERVAL_SEC)
        batch = await client.list_logs(limit=_POLL_BATCH_LIMIT, since=last_seen)
        if not batch:
            continue
        batch_asc = list(reversed(batch))
        _print_batch(batch_asc, header=False, follow=True)
        last_seen = batch_asc[-1].created_at


def _print_batch(items: list[LogOut], *, header: bool, follow: bool = False) -> None:
    """打一批 log;follow 模式走单行格式,非 follow 走 rich table。"""
    if not items:
        if header and not follow:
            Renderer.out("no logs yet")
        return
    if follow:
        for entry in items:
            Renderer.out(_fmt_line(entry))
    else:
        Renderer.table(
            ["id", "created_at", "model", "in→out", "ms", "status"],
            [
                [
                    entry.id[:8] + "…",
                    _fmt_time(entry.created_at),
                    entry.model or "-",
                    f"{entry.input_tokens or 0}→{entry.output_tokens or 0}",
                    entry.latency_ms if entry.latency_ms is not None else "-",
                    entry.status,
                ]
                for entry in items
            ],
        )


def _fmt_time(dt: datetime) -> str:
    # server 存 UTC;本地化后 ISO,和 UI 展示对齐
    return dt.astimezone().isoformat(timespec="seconds")


def _fmt_line(entry: LogOut) -> str:
    ts = _fmt_time(entry.created_at)
    status = f"{entry.status:5s}"
    model = entry.model or "-"
    latency = f"{entry.latency_ms}ms" if entry.latency_ms is not None else "-"
    tokens = f"{entry.input_tokens or 0}→{entry.output_tokens or 0}"
    tail = f" err={entry.error}" if entry.error else ""
    return f"{ts} {status} model={model} {latency} tokens={tokens}{tail}"


def register(app: typer.Typer) -> None:
    app.command("logs", help="最近请求日志;--follow 持续追踪")(logs_cmd)

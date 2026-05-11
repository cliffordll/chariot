"""`chariot logs` — 最近请求流水 + 实时 follow。

0.6.0 库化版:撤旧 ProxyClient,直接走 LogRepo。

两种模式:
- 默认:按 `--limit` 拉最近 N 条(时间降序),打表格退出
- `--follow / -f`:先拉一批 tail(时间升序打),之后 polling(1s 间隔)增量追加;
  Ctrl+C 退出

polling 用 `LogRepo.list_logs(since=<last_created_at>)` 游标拉取,repo 端已做
`>` 过滤,不会重复返回本地已见过的记录。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Annotated

import typer

from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.database.models import LogEntry
from chariot.repos.log_repo import LogRepo

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_POLL_INTERVAL_SEC = 1.0
_POLL_BATCH_LIMIT = 200


def logs_cmd(
    n: Annotated[int, typer.Option("-n", "--limit", help="最多显示多少条")] = 50,
    follow: Annotated[bool, typer.Option("-f", "--follow", help="持续跟踪新日志(Ctrl+C 退出)")] = False,
) -> None:
    """显示请求日志;默认表格打印 N 条,--follow 持续追加增量。"""
    try:
        asyncio.run(_run(n=n, follow=follow))
    except KeyboardInterrupt:
        Renderer.stream_newline()


async def _run(*, n: int, follow: bool) -> None:
    async with installed_runtime() as agent:
        if not follow:
            async with agent.session_maker() as session:
                items = list(await LogRepo(session).list_logs(limit=n, offset=0))
            _print_batch(items, header=True)
            return
        await _follow_loop(agent.session_maker, tail=n)


async def _follow_loop(session_maker: async_sessionmaker[AsyncSession], *, tail: int) -> None:
    """先打 tail 批,再无限 polling since=last_created_at。"""
    async with session_maker() as session:
        initial = list(await LogRepo(session).list_logs(limit=tail, offset=0))
    # repo 返回时间降序;follow 语义希望时间升序(新日志追加在下面)
    initial_asc = list(reversed(initial))
    _print_batch(initial_asc, header=True, follow=True)

    last_seen: datetime | None = initial_asc[-1].created_at if initial_asc else None
    while True:
        await asyncio.sleep(_POLL_INTERVAL_SEC)
        async with session_maker() as session:
            batch = list(await LogRepo(session).list_logs(limit=_POLL_BATCH_LIMIT, offset=0, since=last_seen))
        if not batch:
            continue
        batch_asc = list(reversed(batch))
        _print_batch(batch_asc, header=False, follow=True)
        last_seen = batch_asc[-1].created_at


def _print_batch(items: list[LogEntry], *, header: bool, follow: bool = False) -> None:
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
            ["id", "created_at", "provider", "in→out", "ms", "status"],
            [
                [
                    entry.id[:8] + "…",
                    _fmt_time(entry.created_at),
                    entry.provider or "-",
                    f"{entry.input_tokens or 0}→{entry.output_tokens or 0}",
                    entry.latency_ms if entry.latency_ms is not None else "-",
                    entry.status,
                ]
                for entry in items
            ],
        )


def _fmt_time(dt: datetime) -> str:
    # repo 存 UTC;本地化后 ISO,和 UI 展示对齐
    return dt.astimezone().isoformat(timespec="seconds")


def _fmt_line(entry: LogEntry) -> str:
    ts = _fmt_time(entry.created_at)
    status = f"{entry.status:5s}"
    provider = entry.provider or "-"
    latency = f"{entry.latency_ms}ms" if entry.latency_ms is not None else "-"
    tokens = f"{entry.input_tokens or 0}→{entry.output_tokens or 0}"
    tail = f" err={entry.error}" if entry.error else ""
    return f"{ts} {status} provider={provider} {latency} tokens={tokens}{tail}"


def register(app: typer.Typer) -> None:
    app.command("logs", help="最近请求日志;--follow 持续追踪")(logs_cmd)

"""`chariot stats` — 用量汇总(默认 today,UTC 窗口)。

0.6.0 库化版:撤旧 ProxyClient + 旧 server controller 业务,直接走 LogRepo
聚合。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, get_args

import typer

from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.services.log import LogService

Period = Literal["today", "week", "month"]
_ALLOWED = get_args(Period)


def stats_cmd(
    period: Annotated[str, typer.Argument(help="today | week | month")] = "today",
) -> None:
    if period not in _ALLOWED:
        Renderer.die(f"period 必须是 today/week/month,收到 {period!r}")
        return
    asyncio.run(_run(period))  # type: ignore[arg-type]


async def _run(period: Period) -> None:
    since = _window_start(period)
    async with installed_runtime() as agent:
        total, ok_count, avg_latency = await LogService(agent).aggregate_stats(since=since)
    success_rate = (ok_count / total) if total > 0 else 0.0
    Renderer.kv(
        {
            "period": period,
            "since": since.isoformat(timespec="seconds"),
            "total_requests": total,
            "success_rate": f"{success_rate * 100:.1f}%",
            "avg_latency_ms": f"{avg_latency:.0f}",
        }
    )


def _window_start(period: Period) -> datetime:
    """各 period 的窗口起点(UTC):today=今 0 点 / week=过去 7 天 / month=过去 30 天。"""
    now = datetime.now(UTC)
    if period == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        return now - timedelta(days=7)
    return now - timedelta(days=30)


def register(app: typer.Typer) -> None:
    app.command("stats", help="用量汇总(today/week/month)")(stats_cmd)

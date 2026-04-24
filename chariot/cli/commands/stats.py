"""`chariot stats` — 用量汇总(默认 today,UTC 窗口)。"""

from __future__ import annotations

import asyncio
from typing import Annotated, get_args

import typer

from chariot.cli.core.render import Renderer
from chariot.sdk.client import ProxyClient
from chariot.server.controller.stats import Period

_ALLOWED = get_args(Period)


def stats_cmd(
    period: Annotated[str, typer.Argument(help="today | week | month")] = "today",
) -> None:
    if period not in _ALLOWED:
        Renderer.die(f"period 必须是 today/week/month,收到 {period!r}")
        return
    asyncio.run(_run(period))  # type: ignore[arg-type]


async def _run(period: Period) -> None:
    try:
        async with ProxyClient.discover_session(spawn_if_missing=False) as client:
            s = await client.stats(period=period)
    except RuntimeError as e:
        Renderer.die(f"server 未就绪: {e}")
        return
    Renderer.kv(
        {
            "period": s.period,
            "since": s.since.isoformat(timespec="seconds"),
            "total_requests": s.total_requests,
            "success_rate": f"{s.success_rate * 100:.1f}%",
            "avg_latency_ms": f"{s.avg_latency_ms:.0f}",
        }
    )


def register(app: typer.Typer) -> None:
    app.command("stats", help="用量汇总(today/week/month)")(stats_cmd)

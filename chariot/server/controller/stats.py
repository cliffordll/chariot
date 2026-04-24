"""/admin/stats:用量统计(阶段 4.2)。

v0.1 按 `logs` 表在窗口(today/week/month,UTC)内聚合:
- `total_requests`:记录条数
- `success_rate`:`status=="ok"` 的占比(0.0-1.0),总数 0 时返 0
- `avg_latency_ms`:对非空 `latency_ms` 取平均;无样本返 0

实际 logger 接入前 logs 表为空,本端点返全 0,属正常。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from chariot.server.repository import LogRepoDep

router = APIRouter()

Period = Literal["today", "week", "month"]


class StatsOut(BaseModel):
    period: Period
    since: datetime
    total_requests: int
    success_rate: float
    avg_latency_ms: float


def _period_since(period: Period, *, now: datetime) -> datetime:
    if period == "today":
        return datetime(now.year, now.month, now.day, tzinfo=UTC)
    if period == "week":
        return now - timedelta(days=7)
    return now - timedelta(days=30)


@router.get("/stats", response_model=StatsOut)
async def get_stats(
    repo: LogRepoDep,
    period: Annotated[Period, Query()] = "today",
) -> StatsOut:
    now = datetime.now(UTC)
    since = _period_since(period, now=now)

    total, ok_count, avg_latency = await repo.aggregate_stats(since=since)
    success_rate = (ok_count / total) if total > 0 else 0.0
    return StatsOut(
        period=period,
        since=since,
        total_requests=total,
        success_rate=success_rate,
        avg_latency_ms=avg_latency,
    )

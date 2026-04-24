"""LogRepo:logs 表的数据访问 + 按窗口聚合统计。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import ColumnElement, and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.server.database.models import LogEntry


class LogRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        model: str | None,
        status: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        latency_ms: int | None = None,
        error: str | None = None,
    ) -> LogEntry:
        """插入一条 log;调用方保证字段语义(status ∈ {ok, error, timeout})。"""
        entry = LogEntry(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            status=status,
            error=error,
        )
        self.session.add(entry)
        await self.session.commit()
        await self.session.refresh(entry)
        return entry

    async def list_logs(
        self,
        *,
        limit: int,
        offset: int,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Sequence[LogEntry]:
        """按时间窗口查 log。`since` 严格大于(polling 游标语义)。"""
        filters: list[ColumnElement[bool]] = []
        if since is not None:
            filters.append(LogEntry.created_at > since)
        if until is not None:
            filters.append(LogEntry.created_at <= until)

        stmt = (
            select(LogEntry)
            .order_by(LogEntry.created_at.desc(), LogEntry.id.desc())
            .limit(limit)
            .offset(offset)
        )
        if filters:
            stmt = stmt.where(and_(*filters))

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def aggregate_stats(self, *, since: datetime) -> tuple[int, int, float]:
        """窗口内聚合;返回 (total, ok_count, avg_latency_ms)。无样本时各字段 0。"""
        stmt = select(
            func.count(LogEntry.id),
            func.coalesce(
                func.sum(case((LogEntry.status == "ok", 1), else_=0)),
                0,
            ),
            func.coalesce(func.avg(LogEntry.latency_ms), 0),
        ).where(LogEntry.created_at >= since)
        row = (await self.session.execute(stmt)).one()
        return int(row[0] or 0), int(row[1] or 0), float(row[2] or 0.0)

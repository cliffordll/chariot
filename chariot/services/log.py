"""Log domain service."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from chariot.database.models import LogEntry
from chariot.repos.log_repo import LogRepo
from chariot.services._session_proxy import SessionRepoProxy


class LogService:
    def __init__(self, session_maker: object) -> None:
        self._repo = SessionRepoProxy(session_maker, LogRepo)

    async def create(
        self,
        *,
        provider: str | None,
        status: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        latency_ms: int | None = None,
        error: str | None = None,
    ) -> LogEntry:
        return await self._repo.create(
            provider=provider,
            status=status,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            error=error,
        )

    async def list_logs(
        self,
        *,
        limit: int,
        offset: int,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Sequence[LogEntry]:
        return await self._repo.list_logs(limit=limit, offset=offset, since=since, until=until)

    async def aggregate_stats(self, *, since: datetime) -> tuple[int, int, float]:
        return await self._repo.aggregate_stats(since=since)

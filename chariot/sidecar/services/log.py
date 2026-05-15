"""Sidecar log API surface."""

from __future__ import annotations

from datetime import datetime

from chariot.database.models import LogEntry
from chariot.database.session import init_db
from chariot.services.log import LogService
from chariot.sidecar.runtime import SidecarRuntime


class LogApi:
    def __init__(self, runtime: SidecarRuntime) -> None:
        self._runtime = runtime

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
        sm = await init_db(self._runtime.db_path)
        return await LogService(sm).create(
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
    ) -> list[LogEntry]:
        sm = await init_db(self._runtime.db_path)
        return list(
            await LogService(sm).list_logs(
                limit=limit,
                offset=offset,
                since=since,
                until=until,
            )
        )

    async def aggregate_stats(self, *, since: datetime) -> tuple[int, int, float]:
        sm = await init_db(self._runtime.db_path)
        return await LogService(sm).aggregate_stats(since=since)

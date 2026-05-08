"""/admin/logs:请求流水列表查询。

v0:server 每次 agent.handle() 会落一条 log;CLI / GUI 的 Logs 页从这里拉。

查询参数:
- `limit`(默认 50,上限 500)
- `offset`(默认 0)
- `since` / `until`:ISO 8601 时间戳过滤 `created_at`

响应每条:id / created_at / provider / input_tokens / output_tokens / latency_ms /
status / error。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from chariot.database.models import LogEntry
from chariot.repos import LogRepoDep

router = APIRouter()

_MAX_LIMIT = 500


class LogOut(BaseModel):
    id: str
    created_at: datetime
    provider: str | None
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None
    status: str
    error: str | None

    @classmethod
    def from_row(cls, entry: LogEntry) -> LogOut:
        """从 ORM 行构造响应,免逐字段重复。"""
        return cls(
            id=entry.id,
            created_at=entry.created_at,
            provider=entry.provider,
            input_tokens=entry.input_tokens,
            output_tokens=entry.output_tokens,
            latency_ms=entry.latency_ms,
            status=entry.status,
            error=entry.error,
        )


@router.get("/logs", response_model=list[LogOut])
async def list_logs(
    log_repo: LogRepoDep,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[LogOut]:
    rows = await log_repo.list_logs(
        limit=limit,
        offset=offset,
        since=since,
        until=until,
    )
    return [LogOut.from_row(entry) for entry in rows]

"""AuditRepo:`audit_events` 表的数据访问层(v7)。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.config import ConfigError
from chariot.database.models import AuditEventRow


@dataclass(frozen=True)
class AuditEvent:
    id: str
    event_type: str
    status: str | None
    payload: dict[str, Any]
    created_at: datetime


class AuditRepo:
    """`audit_events` 表的数据访问层。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_events(self, *, limit: int = 50) -> list[AuditEvent]:
        stmt = select(AuditEventRow).order_by(AuditEventRow.created_at.desc(), AuditEventRow.id.desc()).limit(limit)
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._row_to_event(row) for row in rows]

    async def get_event(self, event_id: str) -> AuditEvent | None:
        row = await self.session.get(AuditEventRow, event_id)
        return self._row_to_event(row) if row is not None else None

    async def create(
        self,
        *,
        event_type: str,
        status: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> AuditEvent:
        if not event_type:
            raise ConfigError("audit event_type 必须是非空字符串")
        row = AuditEventRow(
            event_type=event_type,
            status=status,
            payload=self._serialize_json("payload", payload or {}),
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return self._row_to_event(row)

    @staticmethod
    def _serialize_json(label: str, data: dict[str, Any]) -> str:
        try:
            return json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            raise ConfigError(f"audit {label} 不可 JSON 序列化: {e}") from e

    @staticmethod
    def _deserialize_json(label: str, raw: str) -> dict[str, Any]:
        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ConfigError(f"audit {label} JSON 损坏: {e}") from e
        if not isinstance(data, dict):
            raise ConfigError(f"audit {label} JSON 顶层必须是 object")
        return cast(dict[str, Any], data)

    @classmethod
    def _row_to_event(cls, row: AuditEventRow) -> AuditEvent:
        return AuditEvent(
            id=row.id,
            event_type=row.event_type,
            status=row.status,
            payload=cls._deserialize_json("payload", row.payload),
            created_at=row.created_at,
        )

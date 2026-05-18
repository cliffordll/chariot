"""Provider health state access layer."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.database.models import ProviderHealthRow


class ProviderHealthRepo:
    """`provider_health` 表的数据访问层。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, provider_id: str) -> dict[str, Any] | None:
        row = await self.session.get(ProviderHealthRow, provider_id)
        if row is None:
            return None
        return self._row_to_dict(row)

    async def list_entries(self) -> list[dict[str, Any]]:
        stmt = select(ProviderHealthRow).order_by(ProviderHealthRow.last_probe_at.desc())
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._row_to_dict(row) for row in rows]

    async def record_probe(
        self,
        provider_id: str,
        *,
        provider_snapshot: str,
        ok: bool,
        latency_ms: int,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        row = await self.session.get(ProviderHealthRow, provider_id)
        if row is None:
            row = ProviderHealthRow(provider_id=provider_id, provider_snapshot=provider_snapshot)
            self.session.add(row)
        row.provider_snapshot = provider_snapshot
        row.last_ok = 1 if ok else 0
        row.latency_ms = latency_ms
        row.error_code = error_code
        row.error_message = error_message
        await self.session.commit()
        await self.session.refresh(row)
        return self._row_to_dict(row)

    async def clear(self, provider_id: str) -> None:
        await self.session.execute(delete(ProviderHealthRow).where(ProviderHealthRow.provider_id == provider_id))
        await self.session.commit()

    @staticmethod
    def _row_to_dict(row: ProviderHealthRow) -> dict[str, Any]:
        return {
            "provider_id": row.provider_id,
            "provider_snapshot": row.provider_snapshot,
            "last_ok": bool(row.last_ok),
            "latency_ms": row.latency_ms,
            "error_code": row.error_code,
            "error_message": row.error_message,
            "last_probe_at": row.last_probe_at.isoformat() if row.last_probe_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

"""MemoryRepo:`memories` 表的数据访问层(v7)。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.config import ConfigError
from chariot.database.models import MemoryRow


@dataclass(frozen=True)
class MemoryEntry:
    id: str
    kind: str
    text: str
    meta: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class MemoryRepo:
    """`memories` 表的数据访问层。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_entries(self) -> list[MemoryEntry]:
        stmt = select(MemoryRow).order_by(MemoryRow.created_at.desc(), MemoryRow.id.desc())
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._row_to_entry(row) for row in rows]

    async def get_entry(self, entry_id: str) -> MemoryEntry | None:
        row = await self.session.get(MemoryRow, entry_id)
        return self._row_to_entry(row) if row is not None else None

    async def create(
        self,
        *,
        kind: str,
        text: str,
        meta: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        if not kind:
            raise ConfigError("memory kind 必须是非空字符串")
        if not text:
            raise ConfigError("memory text 必须是非空字符串")
        row = MemoryRow(kind=kind, text=text, meta=self._serialize_json("meta", meta or {}))
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return self._row_to_entry(row)

    @staticmethod
    def _serialize_json(label: str, data: dict[str, Any]) -> str:
        try:
            return json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            raise ConfigError(f"memory {label} 不可 JSON 序列化: {e}") from e

    @staticmethod
    def _deserialize_json(label: str, raw: str) -> dict[str, Any]:
        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ConfigError(f"memory {label} JSON 损坏: {e}") from e
        if not isinstance(data, dict):
            raise ConfigError(f"memory {label} JSON 顶层必须是 object")
        return cast(dict[str, Any], data)

    @classmethod
    def _row_to_entry(cls, row: MemoryRow) -> MemoryEntry:
        return MemoryEntry(
            id=row.id,
            kind=row.kind,
            text=row.text,
            meta=cls._deserialize_json("meta", row.meta),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

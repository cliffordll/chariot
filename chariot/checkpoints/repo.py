"""CheckpointRepo:`checkpoints` 表的数据访问层(v7)。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.config import ConfigError
from chariot.database.models import CheckpointRow


@dataclass(frozen=True)
class CheckpointEntry:
    id: str
    name: str
    kind: str
    target: str | None
    payload: dict[str, Any]
    created_at: datetime


class CheckpointRepo:
    """`checkpoints` 表的数据访问层。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_entries(self) -> list[CheckpointEntry]:
        stmt = select(CheckpointRow).order_by(CheckpointRow.created_at.desc(), CheckpointRow.id.desc())
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._row_to_entry(row) for row in rows]

    async def get_entry(self, entry_id: str) -> CheckpointEntry | None:
        row = await self.session.get(CheckpointRow, entry_id)
        return self._row_to_entry(row) if row is not None else None

    async def create(
        self,
        *,
        name: str,
        kind: str,
        target: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> CheckpointEntry:
        if not name:
            raise ConfigError("checkpoint name 必须是非空字符串")
        if not kind:
            raise ConfigError("checkpoint kind 必须是非空字符串")
        row = CheckpointRow(
            name=name,
            kind=kind,
            target=target,
            payload=self._serialize_json("payload", payload or {}),
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return self._row_to_entry(row)

    @staticmethod
    def _serialize_json(label: str, data: dict[str, Any]) -> str:
        try:
            return json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            raise ConfigError(f"checkpoint {label} 不可 JSON 序列化: {e}") from e

    @staticmethod
    def _deserialize_json(label: str, raw: str) -> dict[str, Any]:
        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ConfigError(f"checkpoint {label} JSON 损坏: {e}") from e
        if not isinstance(data, dict):
            raise ConfigError(f"checkpoint {label} JSON 顶层必须是 object")
        return cast(dict[str, Any], data)

    @classmethod
    def _row_to_entry(cls, row: CheckpointRow) -> CheckpointEntry:
        return CheckpointEntry(
            id=row.id,
            name=row.name,
            kind=row.kind,
            target=row.target,
            payload=cls._deserialize_json("payload", row.payload),
            created_at=row.created_at,
        )

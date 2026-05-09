"""SkillRepo:`skills` 表的数据访问层(v7)。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.config import ConfigError
from chariot.database.models import SkillRow


@dataclass(frozen=True)
class SkillEntry:
    id: str
    name: str
    description: str | None
    content: str
    enabled: bool
    meta: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class SkillRepo:
    """`skills` 表的数据访问层。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_entries(self) -> list[SkillEntry]:
        stmt = select(SkillRow).order_by(SkillRow.created_at.desc(), SkillRow.id.desc())
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._row_to_entry(row) for row in rows]

    async def get_entry(self, entry_id: str) -> SkillEntry | None:
        row = await self.session.get(SkillRow, entry_id)
        return self._row_to_entry(row) if row is not None else None

    async def create(
        self,
        *,
        name: str,
        description: str | None = None,
        content: str = "",
        enabled: bool = True,
        meta: dict[str, Any] | None = None,
    ) -> SkillEntry:
        if not name:
            raise ConfigError("skill name 必须是非空字符串")
        row = SkillRow(
            name=name,
            description=description,
            content=content,
            enabled=1 if enabled else 0,
            meta=self._serialize_json("meta", meta or {}),
        )
        self.session.add(row)
        try:
            await self.session.commit()
        except IntegrityError as e:
            await self.session.rollback()
            raise ConfigError(f"skill name {name!r} 已存在") from e
        await self.session.refresh(row)
        return self._row_to_entry(row)

    @staticmethod
    def _serialize_json(label: str, data: dict[str, Any]) -> str:
        try:
            return json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            raise ConfigError(f"skill {label} 不可 JSON 序列化: {e}") from e

    @staticmethod
    def _deserialize_json(label: str, raw: str) -> dict[str, Any]:
        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ConfigError(f"skill {label} JSON 损坏: {e}") from e
        if not isinstance(data, dict):
            raise ConfigError(f"skill {label} JSON 顶层必须是 object")
        return cast(dict[str, Any], data)

    @classmethod
    def _row_to_entry(cls, row: SkillRow) -> SkillEntry:
        return SkillEntry(
            id=row.id,
            name=row.name,
            description=row.description,
            content=row.content,
            enabled=bool(row.enabled),
            meta=cls._deserialize_json("meta", row.meta),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

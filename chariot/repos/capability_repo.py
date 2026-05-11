"""CapabilityRepo:`capabilities` 表的数据访问层(v21,B5 wave 3)。

已知 capability 名(种入 migration 时插的两条):
- `enable_self_mod`:允许 agent 修改 chariot 自身代码(默认 disabled;
  `self_modify_chariot` rule 命中时,enable=True → REQUIRE_APPROVAL,
  enable=False → DENY)
- `yolo`:跳过所有 REQUIRE_APPROVAL 审批,放行 + 写 audit。建议只在沙箱 / CI

读取按 `name → enabled` dict;写入用 upsert(`set_enabled`)。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.config import ConfigError
from chariot.database.models import CapabilityRow


@dataclass(frozen=True)
class CapabilityEntry:
    name: str
    enabled: bool
    updated_at: datetime


class CapabilityRepo:
    """`capabilities` 表的数据访问层。"""

    # 已知 capability 名集合(超出报错,避免 typo 误以为 set 上了)
    KNOWN_NAMES: ClassVar[frozenset[str]] = frozenset({"enable_self_mod", "yolo"})

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_entries(self) -> list[CapabilityEntry]:
        stmt = select(CapabilityRow).order_by(CapabilityRow.name)
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._row_to_entry(row) for row in rows]

    async def get_entry(self, name: str) -> CapabilityEntry | None:
        row = await self.session.get(CapabilityRow, name)
        return self._row_to_entry(row) if row is not None else None

    async def is_enabled(self, name: str) -> bool:
        entry = await self.get_entry(name)
        return entry is not None and entry.enabled

    async def set_enabled(self, name: str, enabled: bool) -> CapabilityEntry:
        if name not in self.KNOWN_NAMES:
            raise ConfigError(f"unknown capability {name!r};已知集:{sorted(self.KNOWN_NAMES)}")
        row = await self.session.get(CapabilityRow, name)
        if row is None:
            row = CapabilityRow(name=name, enabled=1 if enabled else 0)
            self.session.add(row)
        else:
            row.enabled = 1 if enabled else 0
        await self.session.commit()
        await self.session.refresh(row)
        return self._row_to_entry(row)

    @staticmethod
    def _row_to_entry(row: CapabilityRow) -> CapabilityEntry:
        return CapabilityEntry(
            name=row.name,
            enabled=bool(row.enabled),
            updated_at=row.updated_at,
        )

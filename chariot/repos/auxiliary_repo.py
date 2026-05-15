"""`auxiliary_clients` 表的数据访问层(B3 wave 2)。

职责
----
- CRUD `auxiliary_clients` 表 entry
- 反序列化 → `AuxiliaryClientEntry` 数据对象
- 写入校验 / 必填 / 唯一性,IntegrityError 转 `DuplicateAuxiliaryClientName`

非职责
------
- **不**强制 `provider_id` 对应 provider 真存在 —— 业务规则归 service / agent 层
- **不**触发 ContextCompressor reload —— 调用方负责

模块级零自由函数;所有逻辑收在 `AuxiliaryRepo` 类。
"""

from __future__ import annotations

import json
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.exceptions import (
    AuxiliaryClientNotFound,
    ConfigError,
    DuplicateAuxiliaryClientName,
)
from chariot.database.models import AuxiliaryClientRow, ProviderRow
from chariot.models.agent import UNSET, ClearableStr, _UnsetType
from chariot.models.auxiliary import AuxiliaryClientEntry


class AuxiliaryRepo:
    """`auxiliary_clients` 表的数据访问层。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_entries(self) -> list[AuxiliaryClientEntry]:
        """按 created_at 升序返所有 entry(展示顺序稳定)。"""
        stmt = select(AuxiliaryClientRow).order_by(AuxiliaryClientRow.created_at.asc())
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._row_to_entry(r) for r in rows]

    async def get_entry(self, name: str) -> AuxiliaryClientEntry | None:
        row = await self._find_row(name)
        return self._row_to_entry(row) if row is not None else None

    async def create(
        self,
        *,
        name: str,
        provider_id: str,
        model: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> AuxiliaryClientEntry:
        """新增 entry。重名 → DuplicateAuxiliaryClientName;params 不可序列化 → ConfigError。"""
        self._check_name(name)
        self._check_provider(provider_id)
        provider_ref = await self._resolve_provider_ref(provider_id)
        params_json = self._serialize_params(params or {})

        row = AuxiliaryClientRow(name=name, provider_id=provider_ref, model=model, params=params_json)
        self.session.add(row)
        try:
            await self.session.commit()
        except IntegrityError as e:
            await self.session.rollback()
            raise DuplicateAuxiliaryClientName(f"auxiliary client name {name!r} 已存在") from e
        await self.session.refresh(row)
        return self._row_to_entry(row)

    async def update(
        self,
        name: str,
        *,
        provider_id: str | None = None,
        model: ClearableStr = UNSET,
        params: dict[str, Any] | None = None,
    ) -> AuxiliaryClientEntry:
        """改字段。`provider_id`/`params` None 表示不动;`model` 用 UNSET sentinel
        区分 "不传" 与 "显式 clear"(给前端清空字段的能力)。
        """
        row = await self._find_row(name)
        if row is None:
            raise AuxiliaryClientNotFound(f"未知 auxiliary client name: {name!r}")
        if provider_id is not None:
            provider_ref = await self._resolve_provider_ref(provider_id)
            self._check_provider(provider_ref)
            row.provider_id = provider_ref
        if not isinstance(model, _UnsetType):
            row.model = model  # None = clear
        if params is not None:
            row.params = self._serialize_params(params)
        await self.session.commit()
        await self.session.refresh(row)
        return self._row_to_entry(row)

    async def delete(self, name: str) -> None:
        row = await self._find_row(name)
        if row is None:
            raise AuxiliaryClientNotFound(f"未知 auxiliary client name: {name!r}")
        await self.session.delete(row)
        await self.session.commit()

    # ---- 内部 helper ----

    @staticmethod
    def _check_name(name: str) -> None:
        if not name:
            raise ConfigError("auxiliary client name 必须是非空字符串")
        if len(name) > 128:
            raise ConfigError("auxiliary client name 过长(> 128 chars)")

    @staticmethod
    def _check_provider(provider_id: str) -> None:
        if not provider_id:
            raise ConfigError("auxiliary client provider_id 必须是非空字符串")

    @staticmethod
    def _serialize_params(data: dict[str, Any]) -> str:
        try:
            return json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            raise ConfigError(f"auxiliary client params 不可 JSON 序列化: {e}") from e

    @staticmethod
    def _deserialize_params(raw: str) -> dict[str, Any]:
        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ConfigError(f"auxiliary client params JSON 损坏: {e}") from e
        if not isinstance(data, dict):
            raise ConfigError("auxiliary client params JSON 顶层必须是 object")
        return cast(dict[str, Any], data)

    @classmethod
    def _row_to_entry(cls, row: AuxiliaryClientRow) -> AuxiliaryClientEntry:
        return AuxiliaryClientEntry.from_provider_id(
            name=row.name,
            provider_id=row.provider_id,
            model=row.model,
            params=cls._deserialize_params(row.params),
            id=row.id,
        )

    async def _find_row(self, ref: str) -> AuxiliaryClientRow | None:
        stmt = select(AuxiliaryClientRow).where((AuxiliaryClientRow.name == ref) | (AuxiliaryClientRow.id == ref))
        rows = (await self.session.execute(stmt)).scalars().all()
        if not rows:
            return None
        if len(rows) == 1:
            return rows[0]
        exact_id = [row for row in rows if row.id == ref]
        if len(exact_id) == 1:
            return exact_id[0]
        exact_name = [row for row in rows if row.name == ref]
        if len(exact_name) == 1:
            return exact_name[0]
        raise ConfigError(f"auxiliary client 引用 {ref!r} 不唯一,请改用 id")

    async def _resolve_provider_ref(self, ref: str) -> str:
        stmt = select(ProviderRow).where(
            (ProviderRow.id == ref) | (ProviderRow.slug == ref) | (ProviderRow.name == ref)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        if not rows:
            return ref
        if len(rows) > 1:
            exact = [row for row in rows if row.id == ref or row.slug == ref]
            if len(exact) == 1:
                return exact[0].id
            raise ConfigError(f"provider 引用 {ref!r} 不唯一,请改用 slug 或 id")
        return rows[0].id

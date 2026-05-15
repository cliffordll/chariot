"""ProviderRepo:`providers` + `settings` 的数据访问层。"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any, ClassVar, cast

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.config import ConfigError, DuplicateProviderName, ProviderNotFound
from chariot.database.models import ProviderRow, SettingsRow
from chariot.models.provider import ProviderEntry


class ProviderRepo:
    """`providers` 表的数据访问层。"""

    _SEED_NAME: ClassVar[str] = "Mock"
    _SEED_SLUG: ClassVar[str] = "mock"
    _SEED_TYPE: ClassVar[str] = "mock"
    _SEED_OPTIONS: ClassVar[dict[str, Any]] = {}
    _SEED_PARAMS: ClassVar[dict[str, Any]] = {}
    _SLUG_RE: ClassVar[re.Pattern[str]] = re.compile(r"[^a-z0-9_-]+")

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_entries(self) -> list[ProviderEntry]:
        stmt = select(ProviderRow).order_by(ProviderRow.created_at.asc(), ProviderRow.id.asc())
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._row_to_entry(r) for r in rows]

    async def get_entry(self, ref: str) -> ProviderEntry | None:
        row = await self._find_row(ref)
        return self._row_to_entry(row) if row is not None else None

    async def get_by_id(self, provider_id: str) -> ProviderEntry | None:
        row = await self.session.get(ProviderRow, provider_id)
        return self._row_to_entry(row) if row is not None else None

    async def get_by_slug(self, slug: str) -> ProviderEntry | None:
        stmt = select(ProviderRow).where(ProviderRow.slug == slug)
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        return self._row_to_entry(row) if row is not None else None

    async def get_by_legacy_name(self, name: str) -> ProviderEntry | None:
        row = await self._find_row_by_legacy_name(name)
        return self._row_to_entry(row) if row is not None else None

    async def create(
        self,
        *,
        name: str,
        type: str,
        options: dict[str, Any],
        params: dict[str, Any] | None = None,
        slug: str | None = None,
    ) -> ProviderEntry:
        self._check_name(name)
        self._check_type(type)
        if slug is None:
            normalized_slug = await self._next_available_slug(self._default_slug_base(type, name))
        else:
            normalized_slug = self._normalize_slug(slug)
        options_json = self._serialize_json("options", options)
        params_json = self._serialize_json("params", params or {})

        row = ProviderRow(
            slug=normalized_slug,
            name=name,
            type=type,
            options=options_json,
            params=params_json,
        )
        self.session.add(row)
        try:
            await self.session.commit()
        except IntegrityError as e:
            await self.session.rollback()
            raise DuplicateProviderName(f"provider slug {normalized_slug!r} 已存在") from e
        await self.session.refresh(row)
        return self._row_to_entry(row)

    async def update(
        self,
        ref: str,
        *,
        type: str | None = None,
        options: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> ProviderEntry:
        row = await self._find_row(ref)
        if row is None:
            raise ProviderNotFound(f"未知 provider ref: {ref!r}")
        if type is not None:
            self._check_type(type)
            row.type = type
        if options is not None:
            row.options = self._serialize_json("options", options)
        if params is not None:
            row.params = self._serialize_json("params", params)
        await self.session.commit()
        await self.session.refresh(row)
        return self._row_to_entry(row)

    async def rename(self, ref: str, *, new_name: str) -> ProviderEntry:
        row = await self._find_row(ref)
        if row is None:
            raise ProviderNotFound(f"未知 provider ref: {ref!r}")
        self._check_name(new_name)
        row.name = new_name
        await self.session.commit()
        await self.session.refresh(row)
        return self._row_to_entry(row)

    async def change_slug(self, ref: str, *, new_slug: str) -> ProviderEntry:
        row = await self._find_row(ref)
        if row is None:
            raise ProviderNotFound(f"未知 provider ref: {ref!r}")
        row.slug = self._normalize_slug(new_slug)
        try:
            await self.session.commit()
        except IntegrityError as e:
            await self.session.rollback()
            raise DuplicateProviderName(f"provider slug {new_slug!r} 已存在") from e
        await self.session.refresh(row)
        return self._row_to_entry(row)

    async def delete(self, ref: str) -> None:
        row = await self._find_row(ref)
        if row is None:
            raise ProviderNotFound(f"未知 provider ref: {ref!r}")
        await self.session.delete(row)
        await self.session.commit()

    async def get_default_id(self) -> str | None:
        settings = await self._settings_row()
        return settings.default_provider_id if settings is not None else None

    async def get_default(self) -> ProviderEntry | None:
        default_id = await self.get_default_id()
        if default_id is None:
            return None
        return await self.get_by_id(default_id)

    async def set_default(self, ref: str) -> None:
        row = await self._find_row(ref)
        if row is None:
            raise ProviderNotFound(f"未知 provider ref: {ref!r}")
        settings = await self._require_settings_row()
        settings.default_provider_id = row.id
        await self.session.commit()

    async def unset_default(self) -> None:
        settings = await self._require_settings_row()
        settings.default_provider_id = None
        await self.session.commit()

    async def copy(
        self,
        ref: str,
        *,
        as_name: str | None = None,
        as_slug: str | None = None,
    ) -> ProviderEntry:
        src = await self._find_row(ref)
        if src is None:
            raise ProviderNotFound(f"未知 provider ref: {ref!r}")
        target_name = as_name if as_name is not None else f"{src.name} Copy"
        target_slug = as_slug if as_slug is not None else await self._next_copy_slug(src.slug)
        return await self.create(
            name=target_name,
            slug=target_slug,
            type=src.type,
            options=self._deserialize_json("options", src.options),
            params=self._deserialize_json("params", src.params),
        )

    async def seed_if_empty(self) -> None:
        count = await self.session.scalar(select(func.count(ProviderRow.id)))
        if count and count > 0:
            settings = await self._require_settings_row()
            if settings.default_provider_id is None:
                first = (
                    await self.session.execute(
                        select(ProviderRow).order_by(ProviderRow.created_at.asc(), ProviderRow.id.asc()).limit(1)
                    )
                ).scalar_one_or_none()
                if first is not None:
                    settings.default_provider_id = first.id
            await self.session.commit()
            return
        row = ProviderRow(
            slug=self._SEED_SLUG,
            name=self._SEED_NAME,
            type=self._SEED_TYPE,
            options=json.dumps(self._SEED_OPTIONS, ensure_ascii=False),
            params=json.dumps(self._SEED_PARAMS, ensure_ascii=False),
        )
        self.session.add(row)
        await self.session.flush()
        settings = await self._require_settings_row()
        settings.default_provider_id = row.id
        await self.session.commit()

    async def list_rows(self) -> Sequence[ProviderRow]:
        stmt = select(ProviderRow).order_by(ProviderRow.created_at.asc())
        return list((await self.session.execute(stmt)).scalars().all())

    @staticmethod
    def _check_name(name: str) -> None:
        if not name:
            raise ConfigError("provider name 必须是非空字符串")
        if len(name) > 128:
            raise ConfigError("provider name 过长(> 128 chars)")

    @staticmethod
    def _check_type(type_: str) -> None:
        if not type_:
            raise ConfigError("provider type 必须是非空字符串")
        from chariot.providers.registry import ProviderRegistry

        if type_ not in ProviderRegistry.known_types():
            known = ", ".join(sorted(ProviderRegistry.known_types()))
            raise ConfigError(f"未知 provider type: {type_!r};已知类型: {known}")

    @classmethod
    def _normalize_slug(cls, value: str) -> str:
        slug = cls._SLUG_RE.sub("-", value.strip().lower()).strip("-")
        slug = re.sub(r"-{2,}", "-", slug)
        if not slug:
            raise ConfigError("provider slug 必须包含至少一个 a-z / 0-9 字符")
        if len(slug) > 128:
            raise ConfigError("provider slug 过长(> 128 chars)")
        return slug

    @classmethod
    def _default_slug_base(cls, type_: str, name: str) -> str:
        return cls._normalize_slug(f"{type_}-{name}")

    @staticmethod
    def _serialize_json(label: str, data: dict[str, Any]) -> str:
        try:
            return json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            raise ConfigError(f"provider {label} 不可 JSON 序列化: {e}") from e

    @staticmethod
    def _deserialize_json(label: str, raw: str) -> dict[str, Any]:
        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ConfigError(f"{label} JSON 损坏: {e}") from e
        if not isinstance(data, dict):
            raise ConfigError(f"{label} JSON 顶层必须是 object")
        return cast(dict[str, Any], data)

    @classmethod
    def _row_to_entry(cls, row: ProviderRow) -> ProviderEntry:
        return ProviderEntry(
            id=row.id,
            slug=row.slug,
            name=row.name,
            type=row.type,
            options=cls._deserialize_json("options", row.options),
            params=cls._deserialize_json("params", row.params),
        )

    async def _find_row(self, ref: str) -> ProviderRow | None:
        row = await self.session.get(ProviderRow, ref)
        if row is not None:
            return row
        stmt = select(ProviderRow).where(ProviderRow.slug == ref)
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            return row
        return await self._find_row_by_legacy_name(ref)

    async def _find_row_by_legacy_name(self, name: str) -> ProviderRow | None:
        stmt = (
            select(ProviderRow)
            .where(ProviderRow.name == name)
            .order_by(ProviderRow.created_at.asc(), ProviderRow.id.asc())
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        if not rows:
            return None
        if len(rows) > 1:
            raise DuplicateProviderName(f"provider legacy name {name!r} 不再唯一,请改用 slug 或 id")
        return rows[0]

    async def _settings_row(self) -> SettingsRow | None:
        return await self.session.get(SettingsRow, 1)

    async def _require_settings_row(self) -> SettingsRow:
        row = await self._settings_row()
        if row is not None:
            return row
        row = SettingsRow(id=1)
        self.session.add(row)
        await self.session.flush()
        return row

    async def _next_copy_slug(self, src_slug: str) -> str:
        stmt = select(ProviderRow.slug).where(ProviderRow.slug.like(f"{src_slug}-copy%"))
        existing = set((await self.session.execute(stmt)).scalars().all())
        candidate = f"{src_slug}-copy"
        if candidate not in existing:
            return candidate
        i = 2
        while f"{src_slug}-copy-{i}" in existing:
            i += 1
        return f"{src_slug}-copy-{i}"

    async def _next_available_slug(self, base_slug: str) -> str:
        stmt = select(ProviderRow.slug).where(
            (ProviderRow.slug == base_slug) | (ProviderRow.slug.like(f"{base_slug}-%"))
        )
        existing = set((await self.session.execute(stmt)).scalars().all())
        if base_slug not in existing:
            return base_slug
        i = 2
        while f"{base_slug}-{i}" in existing:
            i += 1
        return f"{base_slug}-{i}"

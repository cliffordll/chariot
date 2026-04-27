"""ModelRepo:`models` / `settings` 表的数据访问层。

职责
----
- CRUD `models` 表(model entries)+ `settings` 表(active_model 等)
- 反序列化 → `ModelEntry` 数据对象(给 ModelRegistry.build 用)
- 写入唯一性 / 必填校验,把底层 IntegrityError 转 `ConfigError`(语义层错)

不做的事
--------
- **不**校验 type ∈ ModelRegistry.known_types() —— 业务规则归 controller 层
- **不**校验"删除 active 是否被禁" —— 同上,业务规则归 controller
- **不**触发 Agent reload —— 调用方负责

模块级零自由函数。所有逻辑收在 `ModelRepo` 类里。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.server.config import (
    ConfigError,
    DuplicateModelName,
    ModelEntry,
    ModelNotFound,
)
from chariot.server.database.models import ModelRow, SettingRow

# settings 表里存 active model name 的 key
_ACTIVE_KEY = "active_model"

# seed mock entry 用的常量(表空时插入)
_SEED_NAME = "mock"
_SEED_TYPE = "mock"
_SEED_OPTIONS: dict[str, Any] = {}


class ModelRepo:
    """`models` + `settings` 表的统一数据访问层。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- entries CRUD ----

    async def list_entries(self) -> list[ModelEntry]:
        """按 created_at 升序返所有 entry(展示顺序稳定)。"""
        stmt = select(ModelRow).order_by(ModelRow.created_at.asc(), ModelRow.id.asc())
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._row_to_entry(r) for r in rows]

    async def get_entry(self, name: str) -> ModelEntry | None:
        row = await self._find_row(name)
        return self._row_to_entry(row) if row is not None else None

    async def create(
        self,
        *,
        name: str,
        type: str,
        options: dict[str, Any],
    ) -> ModelEntry:
        """新增 entry。重名 → ConfigError;options 不可序列化 → ConfigError。"""
        self._check_name(name)
        self._check_type(type)
        options_json = self._serialize_options(options)

        row = ModelRow(name=name, type=type, options=options_json)
        self.session.add(row)
        try:
            await self.session.commit()
        except IntegrityError as e:
            await self.session.rollback()
            raise DuplicateModelName(f"model name {name!r} 已存在") from e
        await self.session.refresh(row)
        return self._row_to_entry(row)

    async def update(
        self,
        name: str,
        *,
        type: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> ModelEntry:
        """改 type / options(不允许改 name —— 用 duplicate + delete 模式)。"""
        row = await self._find_row(name)
        if row is None:
            raise ModelNotFound(f"未知 model name: {name!r}")
        if type is not None:
            self._check_type(type)
            row.type = type
        if options is not None:
            row.options = self._serialize_options(options)
        await self.session.commit()
        await self.session.refresh(row)
        return self._row_to_entry(row)

    async def delete(self, name: str) -> None:
        row = await self._find_row(name)
        if row is None:
            raise ModelNotFound(f"未知 model name: {name!r}")
        await self.session.delete(row)
        await self.session.commit()

    async def duplicate(self, name: str, *, as_name: str | None = None) -> ModelEntry:
        """复制 entry。`as_name` 缺省 `<name>_copy`,碰撞自动加序号 `_copy_2 / _3 / ...`。"""
        src = await self._find_row(name)
        if src is None:
            raise ModelNotFound(f"未知 model name: {name!r}")
        target = as_name if as_name is not None else await self._next_copy_name(name)
        self._check_name(target)
        return await self.create(
            name=target,
            type=src.type,
            options=self._deserialize_options(src.options),
        )

    # ---- settings(active_model)----

    async def get_active(self) -> str | None:
        row = await self.session.get(SettingRow, _ACTIVE_KEY)
        return row.value if row is not None and row.value else None

    async def set_active(self, name: str) -> None:
        """设置 active model。校验 name 存在 —— 不允许把 active 指到不存在的 entry。"""
        if await self._find_row(name) is None:
            raise ModelNotFound(f"未知 model name: {name!r}")
        row = await self.session.get(SettingRow, _ACTIVE_KEY)
        if row is None:
            self.session.add(SettingRow(key=_ACTIVE_KEY, value=name))
        else:
            row.value = name
        await self.session.commit()

    async def clear_active(self) -> None:
        """清空 active(走 MockModel fallback)。"""
        row = await self.session.get(SettingRow, _ACTIVE_KEY)
        if row is not None:
            await self.session.delete(row)
            await self.session.commit()

    # ---- 启动期 seed ----

    async def seed_if_empty(self) -> None:
        """models 表空时插入默认 mock entry + active=mock,保证开箱可用。"""
        count = await self.session.scalar(select(func.count(ModelRow.id)))
        if count and count > 0:
            return
        self.session.add(
            ModelRow(name=_SEED_NAME, type=_SEED_TYPE, options=json.dumps(_SEED_OPTIONS)),
        )
        # upsert active
        active_row = await self.session.get(SettingRow, _ACTIVE_KEY)
        if active_row is None:
            self.session.add(SettingRow(key=_ACTIVE_KEY, value=_SEED_NAME))
        else:
            active_row.value = _SEED_NAME
        await self.session.commit()

    # ---- 内部:校验 / 序列化 / 命名 ----

    @staticmethod
    def _check_name(name: str) -> None:
        if not name:
            raise ConfigError("model name 必须是非空字符串")
        if len(name) > 128:
            raise ConfigError("model name 过长(> 128 chars)")

    @staticmethod
    def _check_type(type_: str) -> None:
        if not type_:
            raise ConfigError("model type 必须是非空字符串")

    @staticmethod
    def _serialize_options(options: dict[str, Any]) -> str:
        try:
            return json.dumps(options, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            raise ConfigError(f"model options 不可 JSON 序列化: {e}") from e

    @staticmethod
    def _deserialize_options(raw: str) -> dict[str, Any]:
        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ConfigError(f"options JSON 损坏: {e}") from e
        if not isinstance(data, dict):
            raise ConfigError("options JSON 顶层必须是 object")
        return cast(dict[str, Any], data)

    @classmethod
    def _row_to_entry(cls, row: ModelRow) -> ModelEntry:
        return ModelEntry(
            name=row.name,
            type=row.type,
            options=cls._deserialize_options(row.options),
        )

    async def _find_row(self, name: str) -> ModelRow | None:
        stmt = select(ModelRow).where(ModelRow.name == name)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _next_copy_name(self, src_name: str) -> str:
        """选择第一个不冲突的 `<src>_copy` / `<src>_copy_2` / `<src>_copy_3` ..."""
        # 取所有以 `<src>_copy` 开头的现有 name,集合查询比逐次试更省 round-trip
        stmt = select(ModelRow.name).where(ModelRow.name.like(f"{src_name}_copy%"))
        existing = set((await self.session.execute(stmt)).scalars().all())
        candidate = f"{src_name}_copy"
        if candidate not in existing:
            return candidate
        i = 2
        while f"{src_name}_copy_{i}" in existing:
            i += 1
        return f"{src_name}_copy_{i}"

    # ---- 静态查询(测试 / 调试用)----

    async def count(self) -> int:
        n = await self.session.scalar(select(func.count(ModelRow.id)))
        return int(n or 0)

    async def list_rows(self) -> Sequence[ModelRow]:
        """返原始 ORM rows(给需要 created_at / updated_at 的 caller)。"""
        stmt = select(ModelRow).order_by(ModelRow.created_at.asc())
        return list((await self.session.execute(stmt)).scalars().all())

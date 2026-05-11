"""ProviderRepo:`providers` 表的数据访问层(0.6.0 起;v6 之前表名 `models`,
类名 `ModelRepo`)。

职责
----
- CRUD `providers` 表(provider entries:name / type / options / params)
- 反序列化 → `ProviderEntry` 数据对象(给 ProviderRegistry.build 用)
- 写入唯一性 / 必填校验,把底层 IntegrityError 转 `ConfigError`(语义层错)

不做的事
--------
- **不**校验 type ∈ ProviderRegistry.known_types() —— 业务规则归 controller 层
- **不**触发 AIAgent reload —— 调用方负责

模块级零自由函数。所有逻辑收在 `ProviderRepo` 类里。

0.3.1 路由模型重构后,active 概念删除;`settings` 表也已 drop。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, ClassVar, cast

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.config import (
    ConfigError,
    DuplicateProviderName,
    ProviderEntry,
    ProviderNotFound,
)
from chariot.database.models import ProviderRow


class ProviderRepo:
    """`providers` 表的数据访问层。"""

    # seed mock entry 用的常量(`seed_if_empty` 在表空时插入)
    _SEED_NAME: ClassVar[str] = "mock"
    _SEED_TYPE: ClassVar[str] = "mock"
    _SEED_OPTIONS: ClassVar[dict[str, Any]] = {}
    _SEED_PARAMS: ClassVar[dict[str, Any]] = {}

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- entries CRUD ----

    async def list_entries(self) -> list[ProviderEntry]:
        """按 created_at 升序返所有 entry(展示顺序稳定)。"""
        stmt = select(ProviderRow).order_by(ProviderRow.created_at.asc(), ProviderRow.id.asc())
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._row_to_entry(r) for r in rows]

    async def get_entry(self, name: str) -> ProviderEntry | None:
        row = await self._find_row(name)
        return self._row_to_entry(row) if row is not None else None

    async def create(
        self,
        *,
        name: str,
        type: str,
        options: dict[str, Any],
        params: dict[str, Any] | None = None,
    ) -> ProviderEntry:
        """新增 entry。重名 → DuplicateProviderName;options/params 不可序列化 → ConfigError。"""
        self._check_name(name)
        self._check_type(type)
        options_json = self._serialize_json("options", options)
        params_json = self._serialize_json("params", params or {})

        row = ProviderRow(name=name, type=type, options=options_json, params=params_json)
        self.session.add(row)
        try:
            await self.session.commit()
        except IntegrityError as e:
            await self.session.rollback()
            raise DuplicateProviderName(f"provider name {name!r} 已存在") from e
        await self.session.refresh(row)
        return self._row_to_entry(row)

    async def update(
        self,
        name: str,
        *,
        type: str | None = None,
        options: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> ProviderEntry:
        """改 type / options / params。任一字段 None 表示不动;不允许改 name。"""
        row = await self._find_row(name)
        if row is None:
            raise ProviderNotFound(f"未知 provider name: {name!r}")
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

    async def delete(self, name: str) -> None:
        row = await self._find_row(name)
        if row is None:
            raise ProviderNotFound(f"未知 provider name: {name!r}")
        await self.session.delete(row)
        await self.session.commit()

    # ---- 默认 provider(v7 起;`is_default` 列)----

    async def get_default(self) -> ProviderEntry | None:
        """返当前默认 provider entry;无默认时返 None。

        约束:同一时间至多一行 `is_default=1`(由 `set_default` 原子保证)。
        """
        stmt = select(ProviderRow).where(ProviderRow.is_default == 1)
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        return self._row_to_entry(row) if row is not None else None

    async def set_default(self, name: str) -> None:
        """把 `name` 设为默认 provider(原子:清所有 + 置选中行)。

        `name` 不存在 → `ProviderNotFound`,事务回滚。重复调同一 name 幂等。
        """
        row = await self._find_row(name)
        if row is None:
            raise ProviderNotFound(f"未知 provider name: {name!r}")
        await self.session.execute(update(ProviderRow).where(ProviderRow.is_default == 1).values(is_default=0))
        row.is_default = 1
        await self.session.commit()

    async def unset_default(self) -> None:
        """清掉当前默认(把所有行的 `is_default` 置 0)。无默认时也是 no-op。"""
        await self.session.execute(update(ProviderRow).where(ProviderRow.is_default == 1).values(is_default=0))
        await self.session.commit()

    # ---- copy ----

    async def copy(self, name: str, *, as_name: str | None = None) -> ProviderEntry:
        """复制 entry。`as_name` 缺省 `<name>_copy`,碰撞自动加序号 `_copy_2 / _3 / ...`。

        options 和 params 都从源 entry 拷贝。
        """
        src = await self._find_row(name)
        if src is None:
            raise ProviderNotFound(f"未知 provider name: {name!r}")
        target = as_name if as_name is not None else await self._next_copy_name(name)
        self._check_name(target)
        return await self.create(
            name=target,
            type=src.type,
            options=self._deserialize_json("options", src.options),
            params=self._deserialize_json("params", src.params),
        )

    # ---- 启动期 seed ----

    async def seed_if_empty(self) -> None:
        """providers 表空时插入默认 mock entry,保证开箱可用。

        0.3.1 起 active 概念删除,seed 只插 entry,不再写 active。
        client 必须显式 `body.model = "mock"` 才用 mock(`body.model` 是 Claude
        wire 字段名,内部映射到 provider entry name)。
        """
        count = await self.session.scalar(select(func.count(ProviderRow.id)))
        if count and count > 0:
            return
        self.session.add(
            ProviderRow(
                name=self._SEED_NAME,
                type=self._SEED_TYPE,
                options=json.dumps(self._SEED_OPTIONS),
                params=json.dumps(self._SEED_PARAMS),
                is_default=1,
            ),
        )
        await self.session.commit()

    # ---- 内部:校验 / 序列化 / 命名 ----

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
            name=row.name,
            type=row.type,
            options=cls._deserialize_json("options", row.options),
            params=cls._deserialize_json("params", row.params),
        )

    async def _find_row(self, name: str) -> ProviderRow | None:
        stmt = select(ProviderRow).where(ProviderRow.name == name)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _next_copy_name(self, src_name: str) -> str:
        """选择第一个不冲突的 `<src>_copy` / `<src>_copy_2` / `<src>_copy_3` ..."""
        # 取所有以 `<src>_copy` 开头的现有 name,集合查询比逐次试更省 round-trip
        stmt = select(ProviderRow.name).where(ProviderRow.name.like(f"{src_name}_copy%"))
        existing = set((await self.session.execute(stmt)).scalars().all())
        candidate = f"{src_name}_copy"
        if candidate not in existing:
            return candidate
        i = 2
        while f"{src_name}_copy_{i}" in existing:
            i += 1
        return f"{src_name}_copy_{i}"

    # ---- 静态查询(测试 / 调试用)----

    async def list_rows(self) -> Sequence[ProviderRow]:
        """返原始 ORM rows(给需要 created_at / updated_at 的 caller)。"""
        stmt = select(ProviderRow).order_by(ProviderRow.created_at.asc())
        return list((await self.session.execute(stmt)).scalars().all())

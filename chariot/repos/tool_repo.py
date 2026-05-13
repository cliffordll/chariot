"""ToolRepo:`tools` 表的数据访问层(0.4.0)。

职责
----
- list / get(基本读)
- update(改 enabled / options;**不允许改 name / type**)
 - list_enabled:Agent 装载时只取 enabled=1 的 entries
 - sync_builtin_tools:lifespan startup 调,把代码中的 builtin 工具同步到 DB
   (有则跳过,无则插入,enabled=0)

不暴露的事
----------
- **不开放 create / delete**:0.4.0 tools 表是 4 条 seeded fixture,数量固定。
  开放 CRUD 反而要处理 "用户加的 entry 对应不到 ToolRegistry type" 等边界情况
  (见 docs/DESIGN.md §12)。0.4.x 若加同类多实例(两个 http_get)再开 POST。

错误语义沿用 ModelRepo:`ToolNotFound`(controller 层转 404),JSON 序列化失败
转 `ConfigError`。

模块级零自由函数,所有逻辑收在 `ToolRepo` 类里。
"""

from __future__ import annotations

import json
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.exceptions import ConfigError, ToolNotFound
from chariot.database.models import ToolRow
from chariot.models.tool import ToolEntry
from chariot.tools.builtin._meta import BuiltinToolMeta
from chariot.tools.registry import ToolRegistry


class ToolRepo:
    """`tools` 表的数据访问层。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- 读 ----

    async def list_entries(self) -> list[ToolEntry]:
        """按 created_at 升序返所有 entry(展示顺序稳定)。"""
        stmt = select(ToolRow).order_by(ToolRow.created_at.asc(), ToolRow.id.asc())
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._row_to_entry(r) for r in rows]

    async def list_enabled(self) -> list[ToolEntry]:
        """只取 enabled=1 的 entries(Agent 装载时调)。"""
        stmt = select(ToolRow).where(ToolRow.enabled == 1).order_by(ToolRow.created_at.asc(), ToolRow.id.asc())
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._row_to_entry(r) for r in rows]

    async def get_entry(self, name: str) -> ToolEntry | None:
        row = await self._find_row(name)
        return self._row_to_entry(row) if row is not None else None

    # ---- 写(只允许改 enabled / options)----

    async def update(
        self,
        name: str,
        *,
        enabled: bool | None = None,
        options: dict[str, Any] | None = None,
    ) -> ToolEntry:
        """改 enabled / options。任一字段 None 表示不动;不允许改 name / type。"""
        row = await self._find_row(name)
        if row is None:
            raise ToolNotFound(f"未知 tool name: {name!r}")
        if enabled is not None:
            row.enabled = 1 if enabled else 0
        if options is not None:
            row.options = self._serialize_json("options", options)
        await self.session.commit()
        await self.session.refresh(row)
        return self._row_to_entry(row)

    # ---- 自定义工具 CRUD(0.8.7) ----

    async def create(
        self,
        *,
        name: str,
        type: str,
        enabled: bool = True,
        options: dict[str, Any],
        source: str = "custom",
        description: str = "",
        custom_type: str | None = None,
    ) -> ToolEntry:
        """创建新工具 entry(custom 专用)。"""
        existing = await self._find_row(name)
        if existing is not None:
            raise ConfigError(f"tool name 已存在: {name!r}")
        entry = ToolEntry(
            name=name,
            type=type,
            enabled=enabled,
            options=options,
            source=source,  # type: ignore[arg-type]
            description=description,
            custom_type=custom_type,
        )
        self._probe_entry(entry)
        row = ToolRow(
            name=name,
            type=type,
            enabled=1 if enabled else 0,
            options=self._serialize_json("options", options),
            source=source,
            description=description,
            custom_type=custom_type,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return self._row_to_entry(row)

    async def delete(self, name: str) -> None:
        """删除工具 entry。builtin 不可删。"""
        row = await self._find_row(name)
        if row is None:
            raise ToolNotFound(f"未知 tool name: {name!r}")
        if row.source == "builtin":
            raise ConfigError(f"builtin 工具不可删除: {name!r}")
        await self.session.delete(row)
        await self.session.commit()

    async def update_full(
        self,
        name: str,
        *,
        enabled: bool | None = None,
        options: dict[str, Any] | None = None,
        description: str | None = None,
    ) -> ToolEntry:
        """完整更新(custom 工具专用,可改 description / options)。"""
        row = await self._find_row(name)
        if row is None:
            raise ToolNotFound(f"未知 tool name: {name!r}")
        next_enabled = bool(row.enabled) if enabled is None else enabled
        current_options = self._deserialize_json("options", row.options)
        next_options = current_options if options is None else {**current_options, **options}
        next_description = row.description if description is None else description
        self._probe_entry(
            ToolEntry(
                name=row.name,
                type=row.type,
                enabled=next_enabled,
                options=next_options,
                source=row.source,  # type: ignore[arg-type]
                description=next_description,
                custom_type=row.custom_type,
            )
        )
        if enabled is not None:
            row.enabled = 1 if enabled else 0
        if options is not None:
            row.options = self._serialize_json("options", next_options)
        if description is not None:
            row.description = description
        await self.session.commit()
        await self.session.refresh(row)
        return self._row_to_entry(row)

    # ---- 启动期 sync ----

    async def sync_builtin_tools(self) -> None:
        """同步 builtin 工具到 DB:有则跳过,无则插入(enabled=0)。

        每次启动都调,保证代码里新增的 builtin 工具自动出现在 DB 中,
        同时不覆盖用户已有的 enabled/options 设置。
        """
        existing_names: set[str] = set()
        stmt = select(ToolRow.name).where(ToolRow.source == "builtin")
        rows = (await self.session.execute(stmt)).scalars().all()
        existing_names = set(rows)

        for name, type_, opts in BuiltinToolMeta.seed_entries():
            if name in existing_names:
                continue
            self.session.add(
                ToolRow(
                    name=name,
                    type=type_,
                    enabled=0,
                    options=json.dumps(opts),
                ),
            )
        await self.session.commit()

    # ---- 内部 ----

    @staticmethod
    def _serialize_json(label: str, data: dict[str, Any]) -> str:
        try:
            return json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            raise ConfigError(f"tool {label} 不可 JSON 序列化: {e}") from e

    @staticmethod
    def _deserialize_json(label: str, raw: str) -> dict[str, Any]:
        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ConfigError(f"{label} JSON 损坏: {e}") from e
        if not isinstance(data, dict):
            raise ConfigError(f"{label} JSON 顶层必须是 object")
        return cast(dict[str, Any], data)

    @staticmethod
    def _probe_entry(entry: ToolEntry) -> None:
        tool = ToolRegistry.build(entry)
        tool.schema()

    @classmethod
    def _row_to_entry(cls, row: ToolRow) -> ToolEntry:
        return ToolEntry(
            name=row.name,
            type=row.type,
            enabled=bool(row.enabled),
            options=cls._deserialize_json("options", row.options),
            source=row.source,  # type: ignore[arg-type]
            description=row.description,
            custom_type=row.custom_type,
        )

    async def _find_row(self, name: str) -> ToolRow | None:
        stmt = select(ToolRow).where(ToolRow.name == name)
        return (await self.session.execute(stmt)).scalar_one_or_none()

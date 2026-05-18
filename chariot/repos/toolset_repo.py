"""ToolsetRepo:`toolsets` + `toolset_members` 两表的数据访问层(v16)。

职责
----
- list / get / create / update / delete
- members 操作:list_members / add_member / remove_member / set_members
- create 时支持一并落 members,delete 时由 ON DELETE CASCADE 自动清理

不做的事
--------
- **不校验 tool name 是否存在于 tools 表**:tool 注册是动态的,toolset 可以
  引用未来才会注册的 tool name(跟 agent_profile.tool_profile 弱引用风格一致)
- **不做 enabled 状态切换**:toolset 是 filter,不动 `tool.enabled`
  (详 docs/tool-profile-design.md "Toolset 的两种语义"一节)

错误语义
--------
- duplicate name → `ConfigError`
- update / delete 不存在的 name → `ConfigError`
- JSON 序列化失败 → `ConfigError`

模块级零自由函数,所有逻辑收在 `ToolsetRepo` 类里。
"""

from __future__ import annotations

import json
from typing import Any, cast

from sqlalchemy import delete as sa_delete
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.exceptions import ConfigError
from chariot.database.models import AgentProfileRow, ToolsetMemberRow, ToolsetRow
from chariot.models.toolset import Toolset


class ToolsetRepo:
    """`toolsets` + `toolset_members` 表的数据访问层。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- 读 ----

    async def list_entries(self) -> list[Toolset]:
        stmt = select(ToolsetRow).order_by(ToolsetRow.created_at.asc(), ToolsetRow.name.asc())
        rows = (await self.session.execute(stmt)).scalars().all()
        out: list[Toolset] = []
        for row in rows:
            members = await self._list_members(row.name)
            out.append(self._row_to_entry(row, members))
        return out

    async def get_entry(self, name: str) -> Toolset | None:
        row = await self._find_row(name)
        if row is None:
            return None
        members = await self._list_members(row.name)
        return self._row_to_entry(row, members)

    async def list_members(self, name: str) -> list[str]:
        row = await self._require_row(name)
        return await self._list_members(row.name)

    # ---- 写 ----

    async def create(
        self,
        *,
        name: str,
        description: str | None = None,
        members: list[str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Toolset:
        self._require_non_empty(name, "toolset name")
        row = ToolsetRow(
            name=name,
            description=description,
            meta=self._serialize_json("meta", meta or {}),
        )
        self.session.add(row)
        try:
            await self.session.flush()
        except IntegrityError as e:
            await self.session.rollback()
            raise ConfigError(f"toolset name {name!r} 已存在") from e
        for tool_name in members or []:
            self.session.add(ToolsetMemberRow(toolset_name=name, toolset_id=row.id, tool_name=tool_name))
        await self.session.commit()
        await self.session.refresh(row)
        result_members = await self._list_members(name)
        return self._row_to_entry(row, result_members)

    async def update(
        self,
        name: str,
        *,
        description: str | None = None,
        meta: dict[str, Any] | None = None,
        members: list[str] | None = None,
    ) -> Toolset:
        row = await self._require_row(name)
        if description is not None:
            row.description = description
        if meta is not None:
            row.meta = self._serialize_json("meta", meta)
        if members is not None:
            await self._set_members(name, members)
        await self.session.commit()
        await self.session.refresh(row)
        result_members = await self._list_members(name)
        return self._row_to_entry(row, result_members)

    async def delete(self, name: str) -> None:
        row = await self._require_row(name)
        # SQLite 默认不开 PRAGMA foreign_keys,需手动清成员;SQL 里仍保留
        # ON DELETE CASCADE 作 defense-in-depth(对齐 memory_repo 同款做法)
        await self.session.execute(sa_delete(ToolsetMemberRow).where(ToolsetMemberRow.toolset_name == name))
        await self.session.delete(row)
        await self.session.commit()

    async def rename(self, ref: str, *, new_name: str) -> Toolset:
        row = await self._require_row(ref)
        self._require_non_empty(new_name, "toolset name")
        old_name = row.name
        row.name = new_name
        await self.session.flush()
        if old_name != new_name:
            await self.session.execute(
                update(ToolsetMemberRow).where(ToolsetMemberRow.toolset_id == row.id).values(toolset_name=new_name)
            )
            await self.session.execute(
                update(AgentProfileRow).where(AgentProfileRow.toolset_id == row.id).values(tool_profile=new_name)
            )
        try:
            await self.session.commit()
        except IntegrityError as e:
            await self.session.rollback()
            raise ConfigError(f"toolset name {new_name!r} 已存在") from e
        await self.session.refresh(row)
        result_members = await self._list_members(new_name)
        return self._row_to_entry(row, result_members)

    async def add_member(self, name: str, tool_name: str) -> Toolset:
        row = await self._require_row(name)
        self._require_non_empty(tool_name, "tool name")
        existing = await self.session.execute(
            select(ToolsetMemberRow).where(
                ToolsetMemberRow.toolset_name == row.name,
                ToolsetMemberRow.tool_name == tool_name,
            )
        )
        if existing.scalar_one_or_none() is None:
            self.session.add(ToolsetMemberRow(toolset_name=row.name, toolset_id=row.id, tool_name=tool_name))
            await self.session.commit()
        row = await self._require_row(row.name)
        result_members = await self._list_members(row.name)
        return self._row_to_entry(row, result_members)

    async def remove_member(self, name: str, tool_name: str) -> Toolset:
        row = await self._require_row(name)
        await self.session.execute(
            sa_delete(ToolsetMemberRow).where(
                ToolsetMemberRow.toolset_name == row.name,
                ToolsetMemberRow.tool_name == tool_name,
            )
        )
        await self.session.commit()
        result_members = await self._list_members(row.name)
        return self._row_to_entry(row, result_members)

    # ---- 内部 ----

    async def _list_members(self, name: str) -> list[str]:
        stmt = (
            select(ToolsetMemberRow.tool_name)
            .where(ToolsetMemberRow.toolset_name == name)
            .order_by(ToolsetMemberRow.tool_name.asc())
        )
        return [m for m in (await self.session.execute(stmt)).scalars().all()]

    async def _set_members(self, name: str, members: list[str]) -> None:
        row = await self._require_row(name)
        await self.session.execute(sa_delete(ToolsetMemberRow).where(ToolsetMemberRow.toolset_name == row.name))
        for tool_name in members:
            self._require_non_empty(tool_name, "tool name")
            self.session.add(ToolsetMemberRow(toolset_name=row.name, toolset_id=row.id, tool_name=tool_name))
        await self.session.flush()

    async def _require_row(self, name: str) -> ToolsetRow:
        row = await self._find_row(name)
        if row is None:
            raise ConfigError(f"toolset {name!r} not found")
        return row

    async def _find_row(self, ref: str) -> ToolsetRow | None:
        stmt = select(ToolsetRow).where((ToolsetRow.name == ref) | (ToolsetRow.id == ref))
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
        raise ConfigError(f"toolset 引用 {ref!r} 不唯一,请改用 id")

    @staticmethod
    def _require_non_empty(value: str, label: str) -> None:
        if not value:
            raise ConfigError(f"{label} 必须是非空字符串")

    @staticmethod
    def _serialize_json(label: str, data: Any) -> str:
        try:
            return json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            raise ConfigError(f"toolset {label} 不可 JSON 序列化: {e}") from e

    @classmethod
    def _row_to_entry(cls, row: ToolsetRow, members: list[str]) -> Toolset:
        return Toolset(
            name=row.name,
            id=row.id,
            description=row.description,
            members=tuple(members),
            meta=cls._deserialize_json("meta", row.meta),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _deserialize_json(label: str, raw: str) -> dict[str, Any]:
        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ConfigError(f"toolset {label} JSON 损坏: {e}") from e
        if not isinstance(data, dict):
            raise ConfigError(f"toolset {label} JSON 顶层必须是 object")
        return cast(dict[str, Any], data)

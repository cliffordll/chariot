"""ToolRepo:`tools` 表的数据访问层(0.4.0)。

职责
----
- list / get(基本读)
- update(改 enabled / options;**不允许改 name / type**)
- list_enabled:Agent 装载时只取 enabled=1 的 entries
- seed_if_empty:lifespan startup 调,首次写入 4 条默认 disabled fixture

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
from typing import Any, ClassVar, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.exceptions import ConfigError, ToolNotFound
from chariot.database.models import ToolRow
from chariot.models.tool import ToolEntry


class ToolRepo:
    """`tools` 表的数据访问层。"""

    # 4 条 seeded fixture(0.4.0 全部默认 disabled,见 docs/DESIGN.md §8.1)
    _SEED_FIXTURES: ClassVar[tuple[tuple[str, str, dict[str, Any]], ...]] = (
        ("read_file", "read_file", {"max_bytes": 1048576}),
        ("list_dir", "list_dir", {}),
        ("shell_exec", "shell_exec", {"workdir": "~/.chariot/sandbox", "timeout_s": 30}),
        ("http_get", "http_get", {"allowed_domains": [], "max_bytes": 524288}),
        # B6 wave 3:agent 自发提议 skill。默认 disabled;启用还需 enable_self_mod=True
        # + yolo / 人工 approval 才能跑通 guardrail(self_modify_chariot 规则)
        ("propose_skill", "propose_skill", {}),
    )

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

    # ---- 启动期 seed ----

    async def seed_if_empty(self) -> None:
        """tools 表空时插入 4 条默认 disabled fixture。

        全部 enabled=0,用户必须显式打开 —— "安全优先",见 docs/DESIGN.md §8.1。
        """
        count = await self.session.scalar(select(func.count(ToolRow.id)))
        if count and count > 0:
            return
        for name, type_, opts in self._SEED_FIXTURES:
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

    @classmethod
    def _row_to_entry(cls, row: ToolRow) -> ToolEntry:
        return ToolEntry(
            name=row.name,
            type=row.type,
            enabled=bool(row.enabled),
            options=cls._deserialize_json("options", row.options),
        )

    async def _find_row(self, name: str) -> ToolRow | None:
        stmt = select(ToolRow).where(ToolRow.name == name)
        return (await self.session.execute(stmt)).scalar_one_or_none()

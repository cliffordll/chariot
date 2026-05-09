"""EvalRepo:`eval_runs` + `eval_cases` 表的数据访问层(v8)。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.config import ConfigError
from chariot.database.models import EvalCaseRow, EvalRunRow


@dataclass(frozen=True)
class EvalRunEntry:
    id: str
    name: str | None
    status: str
    summary: dict[str, Any]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class EvalCaseEntry:
    id: str
    suite: str | None
    name: str
    input_payload: dict[str, Any]
    expected: dict[str, Any]
    meta: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class EvalRepo:
    """`eval_runs` + `eval_cases` 表的数据访问层。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_runs(self) -> list[EvalRunEntry]:
        stmt = select(EvalRunRow).order_by(EvalRunRow.created_at.desc(), EvalRunRow.id.desc())
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._row_to_run(row) for row in rows]

    async def get_run(self, run_id: str) -> EvalRunEntry | None:
        row = await self.session.get(EvalRunRow, run_id)
        return self._row_to_run(row) if row is not None else None

    async def create_run(
        self,
        *,
        status: str,
        name: str | None = None,
        summary: dict[str, Any] | None = None,
    ) -> EvalRunEntry:
        if not status:
            raise ConfigError("eval run status 必须是非空字符串")
        row = EvalRunRow(
            name=name,
            status=status,
            summary=self._serialize_json("summary", summary or {}),
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return self._row_to_run(row)

    async def list_cases(self) -> list[EvalCaseEntry]:
        stmt = select(EvalCaseRow).order_by(EvalCaseRow.created_at.desc(), EvalCaseRow.id.desc())
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._row_to_case(row) for row in rows]

    async def get_case(self, case_id: str) -> EvalCaseEntry | None:
        row = await self.session.get(EvalCaseRow, case_id)
        return self._row_to_case(row) if row is not None else None

    async def create_case(
        self,
        *,
        name: str,
        suite: str | None = None,
        input_payload: dict[str, Any] | None = None,
        expected: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> EvalCaseEntry:
        if not name:
            raise ConfigError("eval case name 必须是非空字符串")
        row = EvalCaseRow(
            suite=suite,
            name=name,
            input_payload=self._serialize_json("input_payload", input_payload or {}),
            expected=self._serialize_json("expected", expected or {}),
            meta=self._serialize_json("meta", meta or {}),
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return self._row_to_case(row)

    @staticmethod
    def _serialize_json(label: str, data: dict[str, Any]) -> str:
        try:
            return json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            raise ConfigError(f"eval {label} 不可 JSON 序列化: {e}") from e

    @staticmethod
    def _deserialize_json(label: str, raw: str) -> dict[str, Any]:
        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ConfigError(f"eval {label} JSON 损坏: {e}") from e
        if not isinstance(data, dict):
            raise ConfigError(f"eval {label} JSON 顶层必须是 object")
        return cast(dict[str, Any], data)

    @classmethod
    def _row_to_run(cls, row: EvalRunRow) -> EvalRunEntry:
        return EvalRunEntry(
            id=row.id,
            name=row.name,
            status=row.status,
            summary=cls._deserialize_json("summary", row.summary),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @classmethod
    def _row_to_case(cls, row: EvalCaseRow) -> EvalCaseEntry:
        return EvalCaseEntry(
            id=row.id,
            suite=row.suite,
            name=row.name,
            input_payload=cls._deserialize_json("input_payload", row.input_payload),
            expected=cls._deserialize_json("expected", row.expected),
            meta=cls._deserialize_json("meta", row.meta),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

"""ContextRepo: `context_snapshots` + `context_traces` data access."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.config import ConfigError
from chariot.context.composer import ContextSnapshot
from chariot.database.models import ContextSnapshotRow, ContextTraceRow


@dataclass(frozen=True)
class ContextSnapshotEntry:
    id: str
    conversation_id: str | None
    provider_name: str
    model: str | None
    request: dict[str, Any]
    slices: list[dict[str, Any]]
    source_refs: list[dict[str, Any]]
    context_size: int
    created_at: datetime


@dataclass(frozen=True)
class ContextTraceEntry:
    id: str
    snapshot_id: str
    conversation_id: str | None
    provider_name: str
    model: str | None
    prompt_trace_id: str | None
    policy: dict[str, Any]
    selected_refs: list[dict[str, Any]]
    created_at: datetime


class ContextRepo:
    """`context_snapshots` / `context_traces` table data access."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_snapshots(
        self,
        *,
        conversation_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ContextSnapshotEntry]:
        stmt = select(ContextSnapshotRow).order_by(
            ContextSnapshotRow.created_at.desc(),
            ContextSnapshotRow.id.desc(),
        )
        if conversation_id is not None:
            stmt = stmt.where(ContextSnapshotRow.conversation_id == conversation_id)
        stmt = stmt.limit(limit).offset(offset)
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._snapshot_to_entry(row) for row in rows]

    async def get_snapshot(self, snapshot_id: str) -> ContextSnapshotEntry | None:
        row = await self.session.get(ContextSnapshotRow, snapshot_id)
        return self._snapshot_to_entry(row) if row is not None else None

    async def list_traces(
        self,
        *,
        conversation_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ContextTraceEntry]:
        stmt = select(ContextTraceRow).order_by(
            ContextTraceRow.created_at.desc(),
            ContextTraceRow.id.desc(),
        )
        if conversation_id is not None:
            stmt = stmt.where(ContextTraceRow.conversation_id == conversation_id)
        stmt = stmt.limit(limit).offset(offset)
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._trace_to_entry(row) for row in rows]

    async def get_trace(self, trace_id: str) -> ContextTraceEntry | None:
        row = await self.session.get(ContextTraceRow, trace_id)
        return self._trace_to_entry(row) if row is not None else None

    async def get_trace_by_snapshot_id(self, snapshot_id: str) -> ContextTraceEntry | None:
        stmt = select(ContextTraceRow).where(ContextTraceRow.snapshot_id == snapshot_id)
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        return self._trace_to_entry(row) if row is not None else None

    async def record_snapshot(self, snapshot: ContextSnapshot) -> ContextSnapshotEntry:
        row = ContextSnapshotRow(
            conversation_id=snapshot.conversation_id,
            provider_name=snapshot.provider_name,
            model=snapshot.model,
            request=self._serialize_json("request", snapshot.request),
            slices=self._serialize_json("slices", [self._slice_to_dict(s) for s in snapshot.slices]),
            source_refs=self._serialize_json("source_refs", snapshot.source_refs),
            context_size=snapshot.context_size,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return self._snapshot_to_entry(row)

    async def record_trace(
        self,
        snapshot_id: str,
        *,
        prompt_trace_id: str | None = None,
        policy_name: str = "default_context_policy",
    ) -> ContextTraceEntry:
        snapshot = await self.get_snapshot(snapshot_id)
        if snapshot is None:
            raise ConfigError(f"context snapshot {snapshot_id!r} not found")
        selected_refs = [
            {
                **ref,
                "included": bool(ref.get("present", False)),
                "reason": "available" if ref.get("present", False) else "missing",
            }
            for ref in snapshot.source_refs
        ]
        row = ContextTraceRow(
            snapshot_id=snapshot_id,
            conversation_id=snapshot.conversation_id,
            provider_name=snapshot.provider_name,
            model=snapshot.model,
            prompt_trace_id=prompt_trace_id,
            policy=self._serialize_json(
                "policy",
                {
                    "name": policy_name,
                    "selected_count": len(selected_refs),
                    "excluded_count": sum(1 for ref in selected_refs if not ref.get("included", False)),
                },
            ),
            selected_refs=self._serialize_json("selected_refs", selected_refs),
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return self._trace_to_entry(row)

    async def inspect_context(self, context_id: str) -> dict[str, Any] | None:
        snapshot = await self.get_snapshot(context_id)
        trace: ContextTraceEntry | None
        if snapshot is not None:
            trace = await self.get_trace_by_snapshot_id(snapshot.id)
            return {
                "snapshot": self._snapshot_to_dict(snapshot),
                "trace": self._trace_to_dict(trace) if trace is not None else None,
            }
        trace = await self.get_trace(context_id)
        if trace is None:
            return None
        snapshot = await self.get_snapshot(trace.snapshot_id)
        return {
            "snapshot": self._snapshot_to_dict(snapshot) if snapshot is not None else None,
            "trace": self._trace_to_dict(trace),
        }

    @staticmethod
    def _slice_to_dict(slice_: Any) -> dict[str, Any]:
        return {
            "name": slice_.name,
            "source": slice_.source,
            "content": slice_.content,
        }

    @staticmethod
    def _snapshot_to_dict(entry: ContextSnapshotEntry | None) -> dict[str, Any] | None:
        if entry is None:
            return None
        return {
            "id": entry.id,
            "conversation_id": entry.conversation_id,
            "provider_name": entry.provider_name,
            "model": entry.model,
            "request": entry.request,
            "slices": entry.slices,
            "source_refs": entry.source_refs,
            "context_size": entry.context_size,
            "created_at": entry.created_at.isoformat(),
        }

    @staticmethod
    def _trace_to_dict(entry: ContextTraceEntry | None) -> dict[str, Any] | None:
        if entry is None:
            return None
        return {
            "id": entry.id,
            "snapshot_id": entry.snapshot_id,
            "conversation_id": entry.conversation_id,
            "provider_name": entry.provider_name,
            "model": entry.model,
            "prompt_trace_id": entry.prompt_trace_id,
            "policy": entry.policy,
            "selected_refs": entry.selected_refs,
            "created_at": entry.created_at.isoformat(),
        }

    @staticmethod
    def _snapshot_to_entry(row: ContextSnapshotRow | None) -> ContextSnapshotEntry | None:
        if row is None:
            return None
        return ContextSnapshotEntry(
            id=row.id,
            conversation_id=row.conversation_id,
            provider_name=row.provider_name,
            model=row.model,
            request=cast(dict[str, Any], json.loads(row.request)),
            slices=cast(list[dict[str, Any]], json.loads(row.slices)),
            source_refs=cast(list[dict[str, Any]], json.loads(row.source_refs)),
            context_size=row.context_size,
            created_at=row.created_at,
        )

    @staticmethod
    def _trace_to_entry(row: ContextTraceRow | None) -> ContextTraceEntry | None:
        if row is None:
            return None
        return ContextTraceEntry(
            id=row.id,
            snapshot_id=row.snapshot_id,
            conversation_id=row.conversation_id,
            provider_name=row.provider_name,
            model=row.model,
            prompt_trace_id=row.prompt_trace_id,
            policy=cast(dict[str, Any], json.loads(row.policy)),
            selected_refs=cast(list[dict[str, Any]], json.loads(row.selected_refs)),
            created_at=row.created_at,
        )

    @staticmethod
    def _serialize_json(label: str, data: Any) -> str:
        try:
            return json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            raise ConfigError(f"context {label} not JSON serializable: {e}") from e

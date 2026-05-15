"""Context domain service.

Currently a thin wrapper around `ContextRepo`. Future work will extract
business rules (snapshot retention / trace selection / cross-snapshot
analytics 等)from the repo into this layer; for now the service exists so
callers can depend on a stable domain-layer entry point.
"""

from __future__ import annotations

from typing import Any

from chariot.models.context import ContextSnapshot, ContextSnapshotEntry, ContextTraceEntry
from chariot.repos.context_repo import ContextRepo
from chariot.services._session_proxy import SessionRepoProxy


class ContextService:
    def __init__(self, session_maker: object) -> None:
        self._repo = SessionRepoProxy(session_maker, ContextRepo)

    async def list_snapshots(
        self,
        *,
        conversation_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ContextSnapshotEntry]:
        return await self._repo.list_snapshots(
            conversation_id=conversation_id,
            limit=limit,
            offset=offset,
        )

    async def get_snapshot(self, snapshot_id: str) -> ContextSnapshotEntry | None:
        return await self._repo.get_snapshot(snapshot_id)

    async def list_traces(
        self,
        *,
        conversation_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ContextTraceEntry]:
        return await self._repo.list_traces(
            conversation_id=conversation_id,
            limit=limit,
            offset=offset,
        )

    async def get_trace(self, trace_id: str) -> ContextTraceEntry | None:
        return await self._repo.get_trace(trace_id)

    async def get_trace_by_snapshot_id(self, snapshot_id: str) -> ContextTraceEntry | None:
        return await self._repo.get_trace_by_snapshot_id(snapshot_id)

    async def record_snapshot(self, snapshot: ContextSnapshot) -> ContextSnapshotEntry:
        return await self._repo.record_snapshot(snapshot)

    async def record_trace(
        self,
        snapshot_id: str,
        *,
        prompt_trace_id: str | None = None,
        policy_name: str = "default_context_policy",
    ) -> ContextTraceEntry:
        return await self._repo.record_trace(
            snapshot_id,
            prompt_trace_id=prompt_trace_id,
            policy_name=policy_name,
        )

    async def inspect_context(self, context_id: str) -> dict[str, Any] | None:
        return await self._repo.inspect_context(context_id)

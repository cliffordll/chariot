"""Context API surface for sidecar context-query methods."""

from __future__ import annotations

from typing import Any

from chariot.models.context import ContextSnapshotEntry, ContextTraceEntry
from chariot.repos.context_repo import ContextRepo
from chariot.rpc.jsonrpc import JsonRpcServer, RpcError
from chariot.sidecar.runtime import SidecarRuntime


class ContextApi:
    def __init__(self, runtime: SidecarRuntime) -> None:
        self._runtime = runtime

    async def list_snapshots(
        self,
        session: Any,
        *,
        conversation_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        entries = await ContextRepo(session).list_snapshots(
            conversation_id=conversation_id,
            limit=limit,
            offset=offset,
        )
        return [self._snapshot_to_dict(entry) for entry in entries]

    async def get_snapshot(self, session: Any, *, snapshot_id: str) -> dict[str, Any]:
        entry = await ContextRepo(session).get_snapshot(snapshot_id)
        if entry is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"context snapshot {snapshot_id!r} not found")
        return self._snapshot_to_dict(entry)

    async def list_traces(
        self,
        session: Any,
        *,
        conversation_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        entries = await ContextRepo(session).list_traces(
            conversation_id=conversation_id,
            limit=limit,
            offset=offset,
        )
        return [self._trace_to_dict(entry) for entry in entries]

    async def inspect_context(self, session: Any, *, context_id: str) -> dict[str, Any]:
        entry = await ContextRepo(session).inspect_context(context_id)
        if entry is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"context {context_id!r} not found")
        return entry

    @staticmethod
    def _snapshot_to_dict(entry: ContextSnapshotEntry) -> dict[str, Any]:
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
    def _trace_to_dict(entry: ContextTraceEntry) -> dict[str, Any]:
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

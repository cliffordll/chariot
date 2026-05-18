"""Trace API surface for sidecar inspection methods."""

from __future__ import annotations

from typing import Any

from chariot.models.trace import (
    TraceCheckpoint,
    TraceProviderCall,
    TraceToolCall,
    TraceTree,
    TraceTurn,
    TurnStatus,
)
from chariot.rpc.jsonrpc import JsonRpcServer, RpcError
from chariot.services.trace import TraceService
from chariot.sidecar.runtime import SidecarRuntime


class TraceApi:
    def __init__(self, runtime: SidecarRuntime) -> None:
        self._runtime = runtime

    async def list_turns(
        self,
        *,
        conversation_id: str | None = None,
        task_id: str | None = None,
        provider_snapshot: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        status_enum = self._parse_status(status)
        service = TraceService(self._runtime)
        turns = await service.list_turns(
            conversation_id=conversation_id,
            task_id=task_id,
            provider_snapshot=provider_snapshot,
            status=status_enum,
            limit=limit,
            offset=offset,
        )
        return [self.serialize_turn(turn) for turn in turns]

    async def get_turn(self, *, turn_id: str) -> dict[str, Any]:
        service = TraceService(self._runtime)
        turn = await service.get_turn(turn_id)
        if turn is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"trace turn {turn_id!r} not found")
        return self.serialize_turn(turn)

    async def get_tree(self, *, turn_id: str) -> dict[str, Any]:
        service = TraceService(self._runtime)
        tree = await service.get_tree(turn_id)
        if tree is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"trace turn {turn_id!r} not found")
        return self.serialize_tree(tree)

    async def reconcile_stale(self, *, older_than_seconds: int = 3600) -> dict[str, Any]:
        service = TraceService(self._runtime)
        cleaned = await service.reconcile_stale(older_than_seconds=older_than_seconds)
        return {"cleaned": cleaned}

    @staticmethod
    def _parse_status(value: str | None) -> TurnStatus | None:
        if value is None:
            return None
        try:
            return TurnStatus(value)
        except ValueError as e:
            raise RpcError(JsonRpcServer.ERR_INVALID_PARAMS, f"invalid status: {value!r}") from e

    @classmethod
    def serialize_tree(cls, tree: TraceTree) -> dict[str, Any]:
        return {
            "turn": cls.serialize_turn(tree.turn),
            "provider_calls": [cls.serialize_provider_call(pc) for pc in tree.provider_calls],
            "tool_calls": [cls.serialize_tool_call(tc) for tc in tree.tool_calls],
            "checkpoints": [cls.serialize_checkpoint(cp) for cp in tree.checkpoints],
        }

    @staticmethod
    def serialize_turn(turn: TraceTurn) -> dict[str, Any]:
        return {
            "id": turn.id,
            "conversation_id": turn.conversation_id,
            "agent_profile": turn.agent_profile,
            "task_id": turn.task_id,
            "task_run_id": turn.task_run_id,
            "provider_id": turn.provider_id,
            "provider_snapshot": turn.provider_snapshot,
            "model": turn.model,
            "prompt_trace_id": turn.prompt_trace_id,
            "context_trace_id": turn.context_trace_id,
            "status": turn.status.value,
            "stop_reason": turn.stop_reason,
            "error_type": turn.error_type,
            "error_message": turn.error_message,
            "input_tokens": turn.input_tokens,
            "output_tokens": turn.output_tokens,
            "cache_read_tokens": turn.cache_read_tokens,
            "cache_write_tokens": turn.cache_write_tokens,
            "reasoning_tokens": turn.reasoning_tokens,
            "cost_usd": turn.cost_usd,
            "cost_status": turn.cost_status,
            "duration_ms": turn.duration_ms,
            "started_at": turn.started_at.isoformat(),
            "finished_at": turn.finished_at.isoformat() if turn.finished_at is not None else None,
            "meta": turn.meta,
        }

    @staticmethod
    def serialize_provider_call(pc: TraceProviderCall) -> dict[str, Any]:
        return {
            "id": pc.id,
            "turn_id": pc.turn_id,
            "provider_id": pc.provider_id,
            "provider_snapshot": pc.provider_snapshot,
            "model": pc.model,
            "log_id": pc.log_id,
            "request_summary": pc.request_summary,
            "response_summary": pc.response_summary,
            "started_at": pc.started_at.isoformat(),
            "finished_at": pc.finished_at.isoformat() if pc.finished_at is not None else None,
            "latency_ms": pc.latency_ms,
            "error_type": pc.error_type,
        }

    @staticmethod
    def serialize_tool_call(tc: TraceToolCall) -> dict[str, Any]:
        return {
            "id": tc.id,
            "turn_id": tc.turn_id,
            "provider_call_id": tc.provider_call_id,
            "tool_name": tc.tool_name,
            "arguments": tc.arguments,
            "result_summary": tc.result_summary,
            "duration_ms": tc.duration_ms,
            "status": tc.status.value,
            "error_message": tc.error_message,
            "started_at": tc.started_at.isoformat(),
            "finished_at": tc.finished_at.isoformat() if tc.finished_at is not None else None,
        }

    @staticmethod
    def serialize_checkpoint(cp: TraceCheckpoint) -> dict[str, Any]:
        return {
            "id": cp.id,
            "turn_id": cp.turn_id,
            "kind": cp.kind.value,
            "snapshot_id": cp.snapshot_id,
            "created_at": cp.created_at.isoformat(),
        }

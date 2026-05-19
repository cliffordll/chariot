"""Trace domain service(Phase B1)。

Currently a thin wrapper around `TraceRepo`. Future work will extract
business rules (retention policy / cost rollup / cross-turn analytics
等)from the repo into this layer; for now the service exists so callers
can depend on a stable domain-layer entry point.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from chariot.models.trace import (
    CheckpointKind,
    ToolCallStatus,
    TraceCheckpoint,
    TraceExecutionGroup,
    TraceProviderCall,
    TraceToolCall,
    TraceTree,
    TraceTurn,
    TurnStatus,
)
from chariot.repos.trace_repo import TraceRepo
from chariot.services._session_proxy import SessionRepoProxy


class TraceService:
    def __init__(self, session_maker: object) -> None:
        self._repo = SessionRepoProxy(session_maker, TraceRepo)

    # ---- turn 生命周期 ----

    async def create_turn(
        self,
        *,
        provider_id: str | None = None,
        provider_snapshot: str | None = None,
        conversation_id: str | None = None,
        agent_profile: str | None = None,
        task_id: str | None = None,
        task_run_id: str | None = None,
        model: str | None = None,
        prompt_trace_id: str | None = None,
        context_trace_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> TraceTurn:
        if provider_snapshot is None:
            raise ValueError("provider_snapshot is required")
        return await self._repo.create_turn(
            provider_id=provider_id,
            provider_snapshot=provider_snapshot,
            conversation_id=conversation_id,
            agent_profile=agent_profile,
            task_id=task_id,
            task_run_id=task_run_id,
            model=model,
            prompt_trace_id=prompt_trace_id,
            context_trace_id=context_trace_id,
            meta=meta,
        )

    async def finalize_turn(
        self,
        turn_id: str,
        *,
        status: TurnStatus,
        stop_reason: str | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cache_read_tokens: int | None = None,
        cache_write_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        cost_usd: float | None = None,
        cost_status: str | None = None,
        duration_ms: int | None = None,
    ) -> TraceTurn:
        return await self._repo.finalize_turn(
            turn_id,
            status=status,
            stop_reason=stop_reason,
            error_type=error_type,
            error_message=error_message,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            reasoning_tokens=reasoning_tokens,
            cost_usd=cost_usd,
            cost_status=cost_status,
            duration_ms=duration_ms,
        )

    async def reconcile_stale(self, *, older_than_seconds: int = 3600) -> int:
        return await self._repo.reconcile_stale(older_than_seconds=older_than_seconds)

    async def update_turn_links(
        self,
        turn_id: str,
        *,
        prompt_trace_id: str | None = None,
        context_trace_id: str | None = None,
    ) -> None:
        await self._repo.update_turn_links(
            turn_id,
            prompt_trace_id=prompt_trace_id,
            context_trace_id=context_trace_id,
        )

    # ---- 子事件 ----

    async def record_provider_call(
        self,
        turn_id: str,
        *,
        provider_id: str | None = None,
        provider_snapshot: str | None = None,
        model: str | None = None,
        prompt_trace_id: str | None = None,
        context_trace_id: str | None = None,
        log_id: str | None = None,
        request_summary: dict[str, Any] | None = None,
        response_summary: dict[str, Any] | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        latency_ms: int | None = None,
        error_type: str | None = None,
    ) -> TraceProviderCall:
        if provider_snapshot is None:
            raise ValueError("provider_snapshot is required")
        return await self._repo.record_provider_call(
            turn_id,
            provider_id=provider_id,
            provider_snapshot=provider_snapshot,
            model=model,
            prompt_trace_id=prompt_trace_id,
            context_trace_id=context_trace_id,
            log_id=log_id,
            request_summary=request_summary,
            response_summary=response_summary,
            started_at=started_at,
            finished_at=finished_at,
            latency_ms=latency_ms,
            error_type=error_type,
        )

    async def record_tool_call(
        self,
        turn_id: str,
        *,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        result_summary: dict[str, Any] | None = None,
        provider_call_id: str | None = None,
        duration_ms: int | None = None,
        status: ToolCallStatus = ToolCallStatus.OK,
        error_message: str | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> TraceToolCall:
        return await self._repo.record_tool_call(
            turn_id,
            tool_name=tool_name,
            arguments=arguments,
            result_summary=result_summary,
            provider_call_id=provider_call_id,
            duration_ms=duration_ms,
            status=status,
            error_message=error_message,
            started_at=started_at,
            finished_at=finished_at,
        )

    async def record_checkpoint(
        self,
        turn_id: str,
        *,
        kind: CheckpointKind,
        snapshot_id: str | None = None,
    ) -> TraceCheckpoint:
        return await self._repo.record_checkpoint(turn_id, kind=kind, snapshot_id=snapshot_id)

    # ---- 读 ----

    async def get_turn(self, turn_id: str) -> TraceTurn | None:
        return await self._repo.get_turn(turn_id)

    async def list_provider_calls(self, turn_id: str) -> list[TraceProviderCall]:
        return await self._repo.list_provider_calls(turn_id)

    async def list_tool_calls(self, turn_id: str) -> list[TraceToolCall]:
        return await self._repo.list_tool_calls(turn_id)

    async def list_checkpoints(self, turn_id: str) -> list[TraceCheckpoint]:
        return await self._repo.list_checkpoints(turn_id)

    async def list_turns(
        self,
        *,
        conversation_id: str | None = None,
        task_id: str | None = None,
        provider_snapshot: str | None = None,
        status: TurnStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TraceTurn]:
        return await self._repo.list_turns(
            conversation_id=conversation_id,
            task_id=task_id,
            provider_snapshot=provider_snapshot,
            status=status,
            limit=limit,
            offset=offset,
        )

    async def get_tree(self, turn_id: str) -> TraceTree | None:
        tree = await self._repo.get_tree(turn_id)
        if tree is None:
            return None
        return TraceTree(
            turn=tree.turn,
            provider_calls=tree.provider_calls,
            tool_calls=tree.tool_calls,
            checkpoints=tree.checkpoints,
            execution_groups=self._build_execution_groups(tree.provider_calls, tree.tool_calls),
        )

    async def get_turn_ordinal(self, turn_id: str) -> tuple[int, int] | None:
        return await self._repo.get_turn_ordinal(turn_id)

    @staticmethod
    def _build_execution_groups(
        provider_calls: tuple[TraceProviderCall, ...],
        tool_calls: tuple[TraceToolCall, ...],
    ) -> tuple[TraceExecutionGroup, ...]:
        if not provider_calls:
            return tuple(
                TraceExecutionGroup(index=index, provider_call=None, tool_calls=(tc,))
                for index, tc in enumerate(tool_calls, start=1)
            )

        tool_calls_by_provider: dict[str, list[TraceToolCall]] = {pc.id: [] for pc in provider_calls}
        for tc in tool_calls:
            if tc.provider_call_id and tc.provider_call_id in tool_calls_by_provider:
                tool_calls_by_provider[tc.provider_call_id].append(tc)
                continue
            target = provider_calls[0]
            for pc in provider_calls:
                if tc.started_at >= pc.started_at:
                    target = pc
                else:
                    break
            tool_calls_by_provider[target.id].append(tc)

        groups: list[TraceExecutionGroup] = []
        next_index = 1
        for pc in provider_calls:
            attached_tools = tool_calls_by_provider.get(pc.id, [])
            if not attached_tools:
                groups.append(
                    TraceExecutionGroup(
                        index=next_index,
                        provider_call=pc,
                        tool_calls=(),
                    )
                )
                next_index += 1
                continue

            for tc in attached_tools:
                groups.append(
                    TraceExecutionGroup(
                        index=next_index,
                        provider_call=pc,
                        tool_calls=(tc,),
                    )
                )
                next_index += 1

        return tuple(groups)

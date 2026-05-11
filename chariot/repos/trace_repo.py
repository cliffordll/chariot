"""TraceRepo:`trace_turns` + 三张子表的数据访问层(v17)。

职责
----
- create_turn / update_turn / finalize_turn:turn 生命周期
- record_provider_call / record_tool_call / record_checkpoint:子事件
- list_turns / get_turn / get_tree:读 API(后者拼 TraceTree)

不做的事
--------
- **不调度业务规则**(stale running turn reconcile / retention 都留 service 层)
- **不算 cost**(usage_pricing 在 service 层 / TraceWriter 算完传进来)

错误语义
--------
- update / finalize 不存在的 turn → `ConfigError`
- JSON 序列化失败 → `ConfigError`

模块级零自由函数,所有逻辑收在 `TraceRepo` 类里。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.exceptions import ConfigError
from chariot.database.models import (
    TraceCheckpointRow,
    TraceProviderCallRow,
    TraceToolCallRow,
    TraceTurnRow,
)
from chariot.models.trace import (
    CheckpointKind,
    ToolCallStatus,
    TraceCheckpoint,
    TraceProviderCall,
    TraceToolCall,
    TraceTree,
    TraceTurn,
    TurnStatus,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TraceRepo:
    """`trace_turns` + 三张子表的数据访问层。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- turn 生命周期 ----

    async def create_turn(
        self,
        *,
        provider_name: str,
        conversation_id: str | None = None,
        agent_profile: str | None = None,
        task_id: str | None = None,
        task_run_id: str | None = None,
        model: str | None = None,
        prompt_trace_id: str | None = None,
        context_trace_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> TraceTurn:
        row = TraceTurnRow(
            conversation_id=conversation_id,
            agent_profile=agent_profile,
            task_id=task_id,
            task_run_id=task_run_id,
            provider_name=provider_name,
            model=model,
            prompt_trace_id=prompt_trace_id,
            context_trace_id=context_trace_id,
            status=TurnStatus.RUNNING,
            meta=self._serialize_json("meta", meta or {}),
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return self._turn_row_to_entry(row)

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
        row = await self._require_turn(turn_id)
        row.status = status.value
        row.stop_reason = stop_reason
        row.error_type = error_type
        row.error_message = error_message
        row.input_tokens = input_tokens
        row.output_tokens = output_tokens
        row.cache_read_tokens = cache_read_tokens
        row.cache_write_tokens = cache_write_tokens
        row.reasoning_tokens = reasoning_tokens
        row.cost_usd = cost_usd
        row.cost_status = cost_status
        row.duration_ms = duration_ms
        row.finished_at = _utcnow()
        await self.session.commit()
        await self.session.refresh(row)
        return self._turn_row_to_entry(row)

    async def merge_turn_meta(self, turn_id: str, patch: dict[str, Any]) -> None:
        """把 patch dict shallow-merge 到 trace_turns.meta(给 B3 context 压缩等
        runtime 事件用)。turn 不存在 → silent no-op(best-effort)。"""
        if not patch:
            return
        row = await self.session.get(TraceTurnRow, turn_id)
        if row is None:
            return
        current = self._deserialize_dict("meta", row.meta) if row.meta else {}
        current.update(patch)
        row.meta = self._serialize_json("meta", current)
        await self.session.commit()

    async def reconcile_stale(self, *, older_than_seconds: int = 3600) -> int:
        """把 status=running 且 started_at 超过阈值的 turn 标 cancelled,
        返回处理条数。用于进程崩溃后清理。"""
        stmt = select(TraceTurnRow).where(TraceTurnRow.status == TurnStatus.RUNNING.value)
        rows = (await self.session.execute(stmt)).scalars().all()
        now = _utcnow()
        cleaned = 0
        for row in rows:
            started = row.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            if (now - started).total_seconds() >= older_than_seconds:
                row.status = TurnStatus.CANCELLED.value
                row.error_type = "stale_running"
                row.error_message = "turn left in running state; reconciled by repo"
                row.finished_at = now
                cleaned += 1
        if cleaned:
            await self.session.commit()
        return cleaned

    # ---- 子事件 ----

    async def record_provider_call(
        self,
        turn_id: str,
        *,
        provider_name: str,
        model: str | None = None,
        log_id: str | None = None,
        request_summary: dict[str, Any] | None = None,
        response_summary: dict[str, Any] | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        latency_ms: int | None = None,
        error_type: str | None = None,
    ) -> TraceProviderCall:
        row = TraceProviderCallRow(
            turn_id=turn_id,
            provider_name=provider_name,
            model=model,
            log_id=log_id,
            request_summary=self._serialize_json("request_summary", request_summary or {}),
            response_summary=self._serialize_json("response_summary", response_summary or {}),
            started_at=started_at or _utcnow(),
            finished_at=finished_at,
            latency_ms=latency_ms,
            error_type=error_type,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return self._provider_call_row_to_entry(row)

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
        row = TraceToolCallRow(
            turn_id=turn_id,
            provider_call_id=provider_call_id,
            tool_name=tool_name,
            arguments=self._serialize_json("arguments", arguments or {}),
            result_summary=(
                self._serialize_json("result_summary", result_summary) if result_summary is not None else None
            ),
            duration_ms=duration_ms,
            status=status.value,
            error_message=error_message,
            started_at=started_at or _utcnow(),
            finished_at=finished_at,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return self._tool_call_row_to_entry(row)

    async def record_checkpoint(
        self,
        turn_id: str,
        *,
        kind: CheckpointKind,
        snapshot_id: str | None = None,
    ) -> TraceCheckpoint:
        row = TraceCheckpointRow(
            turn_id=turn_id,
            kind=kind.value,
            snapshot_id=snapshot_id,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return self._checkpoint_row_to_entry(row)

    # ---- 读 ----

    async def get_turn(self, turn_id: str) -> TraceTurn | None:
        row = await self.session.get(TraceTurnRow, turn_id)
        return self._turn_row_to_entry(row) if row is not None else None

    async def list_turns(
        self,
        *,
        conversation_id: str | None = None,
        task_id: str | None = None,
        provider_name: str | None = None,
        status: TurnStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TraceTurn]:
        stmt = select(TraceTurnRow).order_by(TraceTurnRow.started_at.desc(), TraceTurnRow.id.desc())
        if conversation_id is not None:
            stmt = stmt.where(TraceTurnRow.conversation_id == conversation_id)
        if task_id is not None:
            stmt = stmt.where(TraceTurnRow.task_id == task_id)
        if provider_name is not None:
            stmt = stmt.where(TraceTurnRow.provider_name == provider_name)
        if status is not None:
            stmt = stmt.where(TraceTurnRow.status == status.value)
        stmt = stmt.limit(limit).offset(offset)
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._turn_row_to_entry(row) for row in rows]

    async def list_provider_calls(self, turn_id: str) -> list[TraceProviderCall]:
        stmt = (
            select(TraceProviderCallRow)
            .where(TraceProviderCallRow.turn_id == turn_id)
            .order_by(TraceProviderCallRow.started_at.asc(), TraceProviderCallRow.id.asc())
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._provider_call_row_to_entry(row) for row in rows]

    async def list_tool_calls(self, turn_id: str) -> list[TraceToolCall]:
        stmt = (
            select(TraceToolCallRow)
            .where(TraceToolCallRow.turn_id == turn_id)
            .order_by(TraceToolCallRow.started_at.asc(), TraceToolCallRow.id.asc())
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._tool_call_row_to_entry(row) for row in rows]

    async def list_checkpoints(self, turn_id: str) -> list[TraceCheckpoint]:
        stmt = (
            select(TraceCheckpointRow)
            .where(TraceCheckpointRow.turn_id == turn_id)
            .order_by(TraceCheckpointRow.created_at.asc(), TraceCheckpointRow.id.asc())
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._checkpoint_row_to_entry(row) for row in rows]

    async def get_tree(self, turn_id: str) -> TraceTree | None:
        turn = await self.get_turn(turn_id)
        if turn is None:
            return None
        provider_calls = await self.list_provider_calls(turn_id)
        tool_calls = await self.list_tool_calls(turn_id)
        checkpoints = await self.list_checkpoints(turn_id)
        return TraceTree(
            turn=turn,
            provider_calls=tuple(provider_calls),
            tool_calls=tuple(tool_calls),
            checkpoints=tuple(checkpoints),
        )

    # ---- 内部 ----

    async def _require_turn(self, turn_id: str) -> TraceTurnRow:
        row = await self.session.get(TraceTurnRow, turn_id)
        if row is None:
            raise ConfigError(f"trace turn {turn_id!r} not found")
        return row

    @staticmethod
    def _serialize_json(label: str, data: Any) -> str:
        try:
            return json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            raise ConfigError(f"trace {label} 不可 JSON 序列化: {e}") from e

    @staticmethod
    def _deserialize_dict(label: str, raw: str) -> dict[str, Any]:
        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ConfigError(f"trace {label} JSON 损坏: {e}") from e
        if not isinstance(data, dict):
            raise ConfigError(f"trace {label} JSON 顶层必须是 object")
        return cast(dict[str, Any], data)

    @classmethod
    def _turn_row_to_entry(cls, row: TraceTurnRow) -> TraceTurn:
        return TraceTurn(
            id=row.id,
            conversation_id=row.conversation_id,
            agent_profile=row.agent_profile,
            task_id=row.task_id,
            task_run_id=row.task_run_id,
            provider_name=row.provider_name,
            model=row.model,
            prompt_trace_id=row.prompt_trace_id,
            context_trace_id=row.context_trace_id,
            status=TurnStatus(row.status),
            stop_reason=row.stop_reason,
            error_type=row.error_type,
            error_message=row.error_message,
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            cache_read_tokens=row.cache_read_tokens,
            cache_write_tokens=row.cache_write_tokens,
            reasoning_tokens=row.reasoning_tokens,
            cost_usd=row.cost_usd,
            cost_status=row.cost_status,
            duration_ms=row.duration_ms,
            started_at=row.started_at,
            finished_at=row.finished_at,
            meta=cls._deserialize_dict("meta", row.meta),
        )

    @classmethod
    def _provider_call_row_to_entry(cls, row: TraceProviderCallRow) -> TraceProviderCall:
        return TraceProviderCall(
            id=row.id,
            turn_id=row.turn_id,
            provider_name=row.provider_name,
            model=row.model,
            log_id=row.log_id,
            request_summary=cls._deserialize_dict("request_summary", row.request_summary),
            response_summary=cls._deserialize_dict("response_summary", row.response_summary),
            started_at=row.started_at,
            finished_at=row.finished_at,
            latency_ms=row.latency_ms,
            error_type=row.error_type,
        )

    @classmethod
    def _tool_call_row_to_entry(cls, row: TraceToolCallRow) -> TraceToolCall:
        result = cls._deserialize_dict("result_summary", row.result_summary) if row.result_summary else None
        return TraceToolCall(
            id=row.id,
            turn_id=row.turn_id,
            provider_call_id=row.provider_call_id,
            tool_name=row.tool_name,
            arguments=cls._deserialize_dict("arguments", row.arguments),
            result_summary=result,
            duration_ms=row.duration_ms,
            status=ToolCallStatus(row.status),
            error_message=row.error_message,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )

    @staticmethod
    def _checkpoint_row_to_entry(row: TraceCheckpointRow) -> TraceCheckpoint:
        return TraceCheckpoint(
            id=row.id,
            turn_id=row.turn_id,
            kind=CheckpointKind(row.kind),
            snapshot_id=row.snapshot_id,
            created_at=row.created_at,
        )

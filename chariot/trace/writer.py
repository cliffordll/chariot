"""TraceWriter — AIAgent / AgentLoop 用的 trace 写入封装。

设计要点
========
- **唯一写入入口**:对照 `LogWriter` 是唯一日志写入者的约定;新增 trace 写入
  必须走这个类,不允许各处直接拿 `TraceRepo` 写
- **best-effort**:任何 DB 错误内部 catch + log warning,**不向上抛**。trace
  失败不能阻断 chat 主链路
- **handle 配对**:`TurnHandle / ProviderCallHandle / ToolCallHandle` 在
  begin 时记 `started_at`,在 finish 时填 `finished_at / latency_ms / 错误`
- **无状态共享**:`TraceWriter` 只持 `sessionmaker`,每次写入开一个独立 session;
  并发多 turn 互不影响
- **失败降级**:任何 handle 在 begin 阶段就写失败时,id 为 `None`,后续 finish
  无 row 可更新,直接静默跳过(do_nothing)
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from chariot.models.trace import (
    CheckpointKind,
    ToolCallStatus,
    TurnStatus,
)
from chariot.repos.trace_repo import TraceRepo

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


_LOG = logging.getLogger("chariot.trace")


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class ProviderCallHandle:
    """单次 provider.generate 的 trace handle。

    在 `TurnHandle.begin_provider_call(...)` 时构造(已开始计时),调用方在
    provider call 结束后必须 `await handle.finish(...)` 填 finished 字段。
    """

    writer: TraceWriter
    turn_id: str | None
    provider_id: str | None
    provider_name: str
    model: str | None
    started_at: datetime
    _start_perf: float
    id: str | None = None
    _finished: bool = False

    async def finish(
        self,
        *,
        log_id: str | None = None,
        request_summary: dict[str, Any] | None = None,
        response_summary: dict[str, Any] | None = None,
        error_type: str | None = None,
    ) -> None:
        if self._finished or self.turn_id is None or self.writer._disabled:
            return
        self._finished = True
        latency_ms = int((time.perf_counter() - self._start_perf) * 1000)
        finished_at = _utcnow()
        try:
            async with self.writer._session() as session:
                await TraceRepo(session).record_provider_call(
                    self.turn_id,
                    provider_id=self.provider_id,
                    provider_name=self.provider_name,
                    model=self.model,
                    log_id=log_id,
                    request_summary=request_summary or {},
                    response_summary=response_summary or {},
                    started_at=self.started_at,
                    finished_at=finished_at,
                    latency_ms=latency_ms,
                    error_type=error_type,
                )
        except Exception as exc:  # pragma: no cover - best-effort 容错
            _LOG.warning("trace provider_call write failed: %s", exc)


@dataclass
class ToolCallHandle:
    """单次工具调用的 trace handle。"""

    writer: TraceWriter
    turn_id: str | None
    tool_name: str
    arguments: dict[str, Any]
    provider_call_id: str | None
    started_at: datetime
    _start_perf: float
    id: str | None = None
    _finished: bool = False

    async def finish(
        self,
        *,
        status: ToolCallStatus = ToolCallStatus.OK,
        result_summary: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> None:
        if self._finished or self.turn_id is None or self.writer._disabled:
            return
        self._finished = True
        duration_ms = int((time.perf_counter() - self._start_perf) * 1000)
        finished_at = _utcnow()
        try:
            async with self.writer._session() as session:
                await TraceRepo(session).record_tool_call(
                    self.turn_id,
                    tool_name=self.tool_name,
                    arguments=self.arguments,
                    result_summary=result_summary,
                    provider_call_id=self.provider_call_id,
                    duration_ms=duration_ms,
                    status=status,
                    error_message=error_message,
                    started_at=self.started_at,
                    finished_at=finished_at,
                )
        except Exception as exc:  # pragma: no cover - best-effort 容错
            _LOG.warning("trace tool_call write failed: %s", exc)


@dataclass
class TurnHandle:
    """一次 AIAgent.run_chat 的 trace 根 handle。

    `begin_provider_call / begin_tool_call / record_checkpoint` 派生子事件;
    `finalize(...)` 关闭 turn(填 stop_reason / tokens / cost 等)。turn_id
    为 `None` 表示 begin 时写入就失败,后续所有派生都自动退化为 no-op。
    """

    writer: TraceWriter
    turn_id: str | None
    started_at: datetime
    _start_perf: float
    provider_id: str | None = None
    _finalized: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    def begin_provider_call(
        self,
        *,
        provider_name: str,
        model: str | None = None,
    ) -> ProviderCallHandle:
        return ProviderCallHandle(
            writer=self.writer,
            turn_id=self.turn_id,
            provider_id=self.provider_id,
            provider_name=provider_name,
            model=model,
            started_at=_utcnow(),
            _start_perf=time.perf_counter(),
        )

    def begin_tool_call(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        provider_call_id: str | None = None,
    ) -> ToolCallHandle:
        return ToolCallHandle(
            writer=self.writer,
            turn_id=self.turn_id,
            tool_name=tool_name,
            arguments=arguments or {},
            provider_call_id=provider_call_id,
            started_at=_utcnow(),
            _start_perf=time.perf_counter(),
        )

    async def record_checkpoint(
        self,
        *,
        kind: CheckpointKind,
        snapshot_id: str | None = None,
    ) -> None:
        if self.turn_id is None or self.writer._disabled:
            return
        try:
            async with self.writer._session() as session:
                await TraceRepo(session).record_checkpoint(self.turn_id, kind=kind, snapshot_id=snapshot_id)
        except Exception as exc:  # pragma: no cover - best-effort 容错
            _LOG.warning("trace checkpoint write failed: %s", exc)

    async def merge_meta(self, patch: dict[str, Any]) -> None:
        """shallow-merge `patch` 到 trace_turns.meta;同时回写 in-memory `self.meta`
        让调用方读最新值。turn_id=None / disabled / row 已没了 → silent no-op。"""
        if not patch:
            return
        self.meta.update(patch)
        if self.turn_id is None or self.writer._disabled:
            return
        try:
            async with self.writer._session() as session:
                await TraceRepo(session).merge_turn_meta(self.turn_id, patch)
        except Exception as exc:  # pragma: no cover - best-effort 容错
            _LOG.warning("trace merge_turn_meta write failed: %s", exc)

    async def finalize(
        self,
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
    ) -> None:
        if self._finalized or self.turn_id is None or self.writer._disabled:
            return
        self._finalized = True
        duration_ms = int((time.perf_counter() - self._start_perf) * 1000)
        try:
            async with self.writer._session() as session:
                await TraceRepo(session).finalize_turn(
                    self.turn_id,
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
        except Exception as exc:  # pragma: no cover - best-effort 容错
            _LOG.warning("trace finalize_turn write failed: %s", exc)


class TraceWriter:
    """无状态 trace 写入工具,绑 sessionmaker。

    使用方式::

        writer = TraceWriter(sessionmaker)
        turn = await writer.begin_turn(provider_name="mock", conversation_id="X")
        pc = turn.begin_provider_call(provider_name="mock", model="mock-1")
        try:
            # ... 调 provider ...
            await pc.finish(response_summary={"stop_reason": "end_turn"})
        except Exception as e:
            await pc.finish(error_type=type(e).__name__)
            raise
        await turn.finalize(status=TurnStatus.COMPLETED, ...)
    """

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession] | None) -> None:
        self._sessionmaker = sessionmaker
        # sessionmaker=None 时整个 writer 退化为 no-op(stateless chat 不带 DB
        # 的极端情况);所有 handle 仍正常返回但不写入
        self._disabled = sessionmaker is None

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[AsyncSession]:
        assert self._sessionmaker is not None  # _disabled=True 时调用方已早退
        async with self._sessionmaker() as session:
            yield session

    async def begin_turn(
        self,
        *,
        provider_id: str | None = None,
        provider_name: str,
        conversation_id: str | None = None,
        agent_profile: str | None = None,
        task_id: str | None = None,
        task_run_id: str | None = None,
        model: str | None = None,
        prompt_trace_id: str | None = None,
        context_trace_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> TurnHandle:
        started_at = _utcnow()
        start_perf = time.perf_counter()
        if self._disabled:
            return TurnHandle(
                writer=self,
                turn_id=None,
                started_at=started_at,
                _start_perf=start_perf,
                meta=meta or {},
            )
        try:
            async with self._session() as session:
                turn = await TraceRepo(session).create_turn(
                    provider_id=provider_id,
                    provider_name=provider_name,
                    conversation_id=conversation_id,
                    agent_profile=agent_profile,
                    task_id=task_id,
                    task_run_id=task_run_id,
                    model=model,
                    prompt_trace_id=prompt_trace_id,
                    context_trace_id=context_trace_id,
                    meta=meta,
                )
                return TurnHandle(
                    writer=self,
                    turn_id=turn.id,
                    started_at=started_at,
                    _start_perf=start_perf,
                    provider_id=provider_id,
                    meta=meta or {},
                )
        except Exception as exc:  # pragma: no cover - best-effort 容错
            _LOG.warning("trace begin_turn write failed: %s", exc)
            return TurnHandle(
                writer=self,
                turn_id=None,
                started_at=started_at,
                _start_perf=start_perf,
                provider_id=provider_id,
                meta=meta or {},
            )

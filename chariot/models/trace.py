"""Trace 领域数据形态(Phase B1)。

`TraceTurn` 是一次 AIAgent.run_chat 调用的根记录;一个 turn 关联若干
`TraceProviderCall`(每轮调 provider)+ `TraceToolCall`(每次工具调用)+
`TraceCheckpoint`(可选 snapshot)。

设计文档:docs/evolution-design.md §4。

数据粒度:**只存摘要**(决策 1)。完整 request / response payload 仍走
`logs` 表(`TraceProviderCall.log_id` 关联)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class TurnStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ToolCallStatus(StrEnum):
    OK = "ok"
    ERROR = "error"


class CheckpointKind(StrEnum):
    BEFORE_TOOL = "before_tool"
    AFTER_TOOL = "after_tool"
    BEFORE_APPLY = "before_apply"
    MANUAL = "manual"


@dataclass(frozen=True)
class TraceTurn:
    """一次 AIAgent.run_chat 的 trace 根记录。"""

    id: str
    conversation_id: str | None
    agent_profile: str | None
    task_id: str | None
    task_run_id: str | None
    provider_id: str | None
    provider_name_snapshot: str
    model: str | None
    prompt_trace_id: str | None
    context_trace_id: str | None
    status: TurnStatus
    stop_reason: str | None
    error_type: str | None
    error_message: str | None
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    reasoning_tokens: int | None
    cost_usd: float | None
    cost_status: str | None
    duration_ms: int | None
    started_at: datetime
    finished_at: datetime | None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TraceProviderCall:
    """一次 provider.generate 调用的摘要记录。"""

    id: str
    turn_id: str
    provider_id: str | None
    provider_name_snapshot: str
    model: str | None
    log_id: str | None
    request_summary: dict[str, Any]
    response_summary: dict[str, Any]
    started_at: datetime
    finished_at: datetime | None
    latency_ms: int | None
    error_type: str | None


@dataclass(frozen=True)
class TraceToolCall:
    """一次工具调用的摘要记录。"""

    id: str
    turn_id: str
    provider_call_id: str | None
    tool_name: str
    arguments: dict[str, Any]
    result_summary: dict[str, Any] | None
    duration_ms: int | None
    status: ToolCallStatus
    error_message: str | None
    started_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True)
class TraceCheckpoint:
    """turn 内某个关键节点的 snapshot 引用。"""

    id: str
    turn_id: str
    kind: CheckpointKind
    snapshot_id: str | None
    created_at: datetime


@dataclass(frozen=True)
class TraceTree:
    """一次 turn 的完整树形数据(查询用)。"""

    turn: TraceTurn
    provider_calls: tuple[TraceProviderCall, ...] = ()
    tool_calls: tuple[TraceToolCall, ...] = ()
    checkpoints: tuple[TraceCheckpoint, ...] = ()

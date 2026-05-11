"""Task domain model.

Stable Python-side contracts for tasks and task runs. SQL schema and repo
implementation may evolve under these contracts without changing the
service surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TaskKind(StrEnum):
    INTERACTIVE = "interactive"
    BACKGROUND = "background"
    DELEGATED = "delegated"
    SCHEDULED = "scheduled"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    def can_transition_to(self, target: TaskStatus) -> bool:
        allowed = {
            TaskStatus.QUEUED: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
            TaskStatus.RUNNING: {
                TaskStatus.PAUSED,
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            },
            TaskStatus.PAUSED: {
                TaskStatus.RUNNING,
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            },
            TaskStatus.COMPLETED: set(),
            TaskStatus.FAILED: set(),
            TaskStatus.CANCELLED: set(),
        }
        return target in allowed[self]


class TaskRunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class TaskCreate:
    goal: str
    kind: TaskKind = TaskKind.INTERACTIVE
    agent_profile: str | None = None
    parent_task_id: str | None = None
    owner: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Task:
    id: str
    goal: str
    kind: TaskKind
    status: TaskStatus
    agent_profile: str | None = None
    parent_task_id: str | None = None
    owner: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    def with_status(self, status: TaskStatus) -> Task:
        if not self.status.can_transition_to(status) and self.status != status:
            raise ValueError(f"invalid task status transition: {self.status} -> {status}")
        return replace(self, status=status, updated_at=_utcnow())


@dataclass(frozen=True)
class TaskRunCreate:
    task_id: str
    resume_from_run_id: str | None = None
    trigger: str = "manual"
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskRun:
    id: str
    task_id: str
    status: TaskRunStatus
    trigger: str = "manual"
    resume_from_run_id: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    started_at: datetime = field(default_factory=_utcnow)
    finished_at: datetime | None = None

    def finish(
        self,
        *,
        status: TaskRunStatus,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> TaskRun:
        if self.status != TaskRunStatus.RUNNING:
            raise ValueError(f"task run {self.id!r} is not running")
        if status == TaskRunStatus.RUNNING:
            raise ValueError("finished run cannot remain in running status")
        return replace(
            self,
            status=status,
            result=result or {},
            error=error,
            finished_at=_utcnow(),
        )


# Delegation: 用 TaskService.delegate() 把"创建一组子 task 并保留父子关系"做成一次显式动作。
# 不引入新执行能力 —— 实际创建子 task 仍走 TaskService.create_child_task。
@dataclass(frozen=True)
class DelegatedTaskSpec:
    goal: str
    agent_profile: str | None = None
    owner: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DelegationRequest:
    parent_task_id: str
    tasks: tuple[DelegatedTaskSpec, ...]
    reason: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DelegationResult:
    parent_task_id: str
    child_task_ids: tuple[str, ...]
    requested: int
    created: int

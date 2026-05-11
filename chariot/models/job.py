"""Scheduled job domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ScheduledJob:
    name: str
    goal: str
    cron: str
    enabled: bool = True
    agent_profile: str | None = None
    last_run_status: str | None = None
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True)
class JobRunRecord:
    id: str
    job_name: str
    task_id: str | None = None
    status: str = "queued"
    error: str | None = None
    started_at: datetime = field(default_factory=_utcnow)
    finished_at: datetime | None = None

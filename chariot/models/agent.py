"""Agent profile domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class AgentProfile:
    name: str
    role: str
    prompt_bundle: str | None = None
    tool_profile: str | None = None
    provider_profile: str | None = None
    budget: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

"""Delegation contracts.

Delegation creates and links child tasks. It does not execute business tools
itself; child-task execution can later load tools from the child agent profile.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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

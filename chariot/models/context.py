"""Context 领域数据形态。

- `ContextSlice` / `ContextSnapshot`:`ContextComposer` 构造出的"一次请求的
  上下文快照";原属 `chariot/context/composer.py`,0.7.2 起迁来
- `ContextSnapshotEntry` / `ContextTraceEntry`:`ContextRepo` 读写
  `context_snapshots` / `context_traces` 表后还原的业务对象;原属
  `chariot/repos/context_repo.py`,0.7.2 起迁来
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ContextSlice:
    name: str
    source: str
    content: Any | None = None


@dataclass(frozen=True)
class ContextSnapshot:
    conversation_id: str | None
    provider_snapshot: str
    model: str | None
    request: dict[str, Any]
    slices: list[ContextSlice]
    source_refs: list[dict[str, Any]]
    context_size: int
    provider_id: str | None = None


@dataclass(frozen=True)
class ContextSnapshotEntry:
    id: str
    conversation_id: str | None
    provider_id: str | None
    provider_snapshot: str
    model: str | None
    request: dict[str, Any]
    slices: list[dict[str, Any]]
    source_refs: list[dict[str, Any]]
    context_size: int
    created_at: datetime


@dataclass(frozen=True)
class ContextTraceEntry:
    id: str
    snapshot_id: str
    conversation_id: str | None
    provider_id: str | None
    provider_snapshot: str
    model: str | None
    prompt_trace_id: str | None
    policy: dict[str, Any]
    selected_refs: list[dict[str, Any]]
    created_at: datetime

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
class ContextBundleEntry:
    id: str
    name: str
    description: str | None
    is_active: bool
    version_count: int
    active_version: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ContextVersionEntry:
    id: str
    bundle_id: str
    bundle_name: str
    version: str
    spec: dict[str, Any]
    is_active: bool
    created_at: datetime
    updated_at: datetime


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
    bundle_id: str | None
    bundle_name: str | None
    version_id: str | None
    version: str | None
    conversation_id: str | None
    provider_id: str | None
    provider_snapshot: str
    model: str | None
    prompt_trace_id: str | None
    policy: dict[str, Any]
    selected_refs: list[dict[str, Any]]
    created_at: datetime

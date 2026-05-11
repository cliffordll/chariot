"""Memory 领域数据形态。

`MemoryEntry` / `MemoryEventEntry` / `MemoryLinkEntry` 是长期记忆的业务对象,
与持久层(`chariot.database.models.MemoryRow` / `MemoryEventRow` / `MemoryLinkRow`)
解耦。0.7.2 起从 `chariot/repos/memory_repo.py` 迁来。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class MemoryEntry:
    id: str
    kind: str
    text: str
    meta: dict[str, Any]
    pinned: bool
    archived: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class MemoryEventEntry:
    id: str
    memory_id: str
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class MemoryLinkEntry:
    id: str
    memory_id: str
    link_type: str
    link_value: str
    created_at: datetime

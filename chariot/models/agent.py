"""Agent profile domain model + 'unset vs cleared' sentinel。

Update 路径需要区分两种 None:
- 字段没传(skip)→ `UNSET`
- 字段传 null(用户清空 binding)→ `None`,落库 set NULL

Sentinel 放 model 层是为了让 repo / service / sidecar adapter 三层都能 import,
避免 service ↔ repo 循环依赖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final


def _utcnow() -> datetime:
    return datetime.now(UTC)


class _UnsetType:
    """Singleton sentinel for 'field not provided' (区别于 None=显式清空)。"""

    _instance: _UnsetType | None = None

    def __new__(cls) -> _UnsetType:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "<UNSET>"

    def __bool__(self) -> bool:
        return False


UNSET: Final = _UnsetType()
ClearableStr = str | None | _UnsetType


@dataclass(frozen=True)
class AgentProfile:
    name: str
    role: str
    prompt_bundle: str | None = None
    tool_profile: str | None = None
    provider_profile: str | None = None
    budget: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    # B4 wave 3:reflection 控制(透传给 ChatRequest.reflection_* 字段)
    reflection_enabled: bool = False
    reflection_max_retries: int = 2
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

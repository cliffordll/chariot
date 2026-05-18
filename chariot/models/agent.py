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
    prompt_id: str | None = None
    toolset_id: str | None = None
    provider_id: str | None = None
    budget: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    # B4 wave 3:reflection 控制(透传给 ChatRequest.reflection_* 字段)
    reflection_enabled: bool = False
    reflection_max_retries: int = 2
    # B6 wave 2:agent_profile 预绑 skill;chat 不显式 --skill 时透传给
    # ChatRequest.skill;dangling reference(skill 不存在 / disabled)走 fallback。
    default_skill: str | None = None
    id: str = ""
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    @classmethod
    def from_provider_id(
        cls,
        *,
        name: str,
        role: str,
        provider_id: str | None = None,
        prompt_id: str | None = None,
        toolset_id: str | None = None,
        budget: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        reflection_enabled: bool = False,
        reflection_max_retries: int = 2,
        default_skill: str | None = None,
        id: str = "",
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> AgentProfile:
        return cls(
            name=name,
            role=role,
            prompt_id=prompt_id,
            toolset_id=toolset_id,
            provider_id=provider_id,
            budget=budget or {},
            meta=meta or {},
            reflection_enabled=reflection_enabled,
            reflection_max_retries=reflection_max_retries,
            default_skill=default_skill,
            id=id,
            created_at=created_at or _utcnow(),
            updated_at=updated_at or _utcnow(),
        )

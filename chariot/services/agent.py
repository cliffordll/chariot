"""Agent profile domain service。

Update 语义需要区分两种 None:
- 字段没传(skip)→ `UNSET` 哨兵
- 字段传 null(用户清空 binding)→ `None`,落库 set NULL

只有三个 binding 字段(prompt_bundle / tool_profile / provider_profile)
clearable;role/budget/meta 没有"清空到 NULL"语义,仍用 `None=skip`。
B4 wave 3 加 reflection 字段(`reflection_enabled` / `reflection_max_retries`),
仍走 `None=skip` 语义(bool / int 默认值有意义,不需要 clear)。
Sentinel 定义在 `chariot.models.agent`,跨层共享。
"""

from __future__ import annotations

from typing import Protocol

from chariot.models.agent import UNSET, AgentProfile, ClearableStr, _UnsetType

__all__ = ["UNSET", "AgentProfile", "AgentProfileStore", "AgentService", "ClearableStr"]


class AgentProfileStore(Protocol):
    async def list_profiles(self) -> list[AgentProfile]: ...

    async def get_profile(self, name: str) -> AgentProfile | None: ...

    async def create_agent_profile(
        self,
        *,
        name: str,
        role: str,
        prompt_bundle: str | None = None,
        tool_profile: str | None = None,
        provider_profile: str | None = None,
        budget: dict[str, object] | None = None,
        meta: dict[str, object] | None = None,
        reflection_enabled: bool = False,
        reflection_max_retries: int = 2,
    ) -> AgentProfile: ...

    async def update_agent_profile(
        self,
        *,
        name: str,
        role: str | None = None,
        prompt_bundle: ClearableStr = UNSET,
        tool_profile: ClearableStr = UNSET,
        provider_profile: ClearableStr = UNSET,
        budget: dict[str, object] | None = None,
        meta: dict[str, object] | None = None,
        reflection_enabled: bool | None = None,
        reflection_max_retries: int | None = None,
    ) -> AgentProfile: ...

    async def delete_agent_profile(self, name: str) -> None: ...


class AgentService:
    def __init__(self, store: AgentProfileStore) -> None:
        self._store = store

    async def list_agents(self) -> list[AgentProfile]:
        return await self._store.list_profiles()

    async def get_agent(self, name: str) -> AgentProfile | None:
        return await self._store.get_profile(name)

    async def create_agent(
        self,
        *,
        name: str,
        role: str,
        prompt_bundle: str | None = None,
        tool_profile: str | None = None,
        provider_profile: str | None = None,
        budget: dict[str, object] | None = None,
        meta: dict[str, object] | None = None,
        reflection_enabled: bool = False,
        reflection_max_retries: int = 2,
    ) -> AgentProfile:
        return await self._store.create_agent_profile(
            name=name,
            role=role,
            prompt_bundle=prompt_bundle,
            tool_profile=tool_profile,
            provider_profile=provider_profile,
            budget=budget,
            meta=meta,
            reflection_enabled=reflection_enabled,
            reflection_max_retries=reflection_max_retries,
        )

    async def update_agent(
        self,
        *,
        name: str,
        role: str | None = None,
        prompt_bundle: ClearableStr = UNSET,
        tool_profile: ClearableStr = UNSET,
        provider_profile: ClearableStr = UNSET,
        budget: dict[str, object] | None = None,
        meta: dict[str, object] | None = None,
        reflection_enabled: bool | None = None,
        reflection_max_retries: int | None = None,
    ) -> AgentProfile:
        agent = await self._store.get_profile(name)
        if agent is None:
            raise ValueError(f"agent profile {name!r} not found")
        if (
            role is None
            and isinstance(prompt_bundle, _UnsetType)
            and isinstance(tool_profile, _UnsetType)
            and isinstance(provider_profile, _UnsetType)
            and budget is None
            and meta is None
            and reflection_enabled is None
            and reflection_max_retries is None
        ):
            raise ValueError("no agent fields provided to update")
        return await self._store.update_agent_profile(
            name=name,
            role=role,
            prompt_bundle=prompt_bundle,
            tool_profile=tool_profile,
            provider_profile=provider_profile,
            budget=budget,
            meta=meta,
            reflection_enabled=reflection_enabled,
            reflection_max_retries=reflection_max_retries,
        )

    async def delete_agent(self, name: str) -> None:
        agent = await self._store.get_profile(name)
        if agent is None:
            raise ValueError(f"agent profile {name!r} not found")
        await self._store.delete_agent_profile(name)

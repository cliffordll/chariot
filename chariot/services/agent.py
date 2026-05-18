"""Agent profile domain service。

Update 语义需要区分两种 None:
- 字段没传(skip)→ `UNSET` 哨兵
- 字段传 null(用户清空 binding)→ `None`,落库 set NULL

只有三个 binding 字段(prompt_id / toolset_id / provider_id)。
其中 prompt / toolset 绑定只使用 `prompt_id` / `toolset_id`。
clearable;role/budget/meta 没有"清空到 NULL"语义,仍用 `None=skip`。
B4 wave 3 加 reflection 字段(`reflection_enabled` / `reflection_max_retries`),
仍走 `None=skip` 语义(bool / int 默认值有意义,不需要 clear)。
Sentinel 定义在 `chariot.models.agent`,跨层共享。
"""

from __future__ import annotations

from typing import Protocol

from chariot.models.agent import UNSET, AgentProfile, ClearableStr, _UnsetType
from chariot.repos.task_repo import TaskRepo
from chariot.services._session_proxy import SessionRepoProxy

__all__ = ["UNSET", "AgentProfile", "AgentProfileStore", "AgentService", "ClearableStr"]


class AgentProfileStore(Protocol):
    async def list_profiles(self) -> list[AgentProfile]: ...

    async def get_profile(self, name: str) -> AgentProfile | None: ...

    async def create_agent_profile(
        self,
        *,
        name: str,
        role: str,
        prompt_id: str | None = None,
        toolset_id: str | None = None,
        provider_id: str | None = None,
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
        prompt_id: ClearableStr = UNSET,
        toolset_id: ClearableStr = UNSET,
        provider_id: ClearableStr = UNSET,
        budget: dict[str, object] | None = None,
        meta: dict[str, object] | None = None,
        reflection_enabled: bool | None = None,
        reflection_max_retries: int | None = None,
    ) -> AgentProfile: ...

    async def delete_agent_profile(self, name: str) -> None: ...

    async def rename_agent_profile(self, ref: str, *, new_name: str) -> AgentProfile: ...


class AgentService:
    def __init__(self, session_maker: object) -> None:
        self._store = SessionRepoProxy(session_maker, TaskRepo)

    async def list_agents(self) -> list[AgentProfile]:
        return await self._store.list_profiles()

    async def get_agent(self, name: str) -> AgentProfile | None:
        return await self._store.get_profile(name)

    async def create_agent(
        self,
        *,
        name: str,
        role: str,
        prompt_id: str | None = None,
        toolset_id: str | None = None,
        provider_id: str | None = None,
        budget: dict[str, object] | None = None,
        meta: dict[str, object] | None = None,
        reflection_enabled: bool = False,
        reflection_max_retries: int = 2,
    ) -> AgentProfile:
        return await self._store.create_agent_profile(
            name=name,
            role=role,
            prompt_id=prompt_id,
            toolset_id=toolset_id,
            provider_id=provider_id,
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
        prompt_id: ClearableStr = UNSET,
        toolset_id: ClearableStr = UNSET,
        provider_id: ClearableStr = UNSET,
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
            and isinstance(prompt_id, _UnsetType)
            and isinstance(toolset_id, _UnsetType)
            and isinstance(provider_id, _UnsetType)
            and budget is None
            and meta is None
            and reflection_enabled is None
            and reflection_max_retries is None
        ):
            raise ValueError("no agent fields provided to update")
        return await self._store.update_agent_profile(
            name=name,
            role=role,
            prompt_id=prompt_id,
            toolset_id=toolset_id,
            provider_id=provider_id,
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

    async def rename_agent(self, ref: str, new_name: str) -> AgentProfile:
        agent = await self._store.get_profile(ref)
        if agent is None:
            raise ValueError(f"agent profile {ref!r} not found")
        return await self._store.rename_agent_profile(ref, new_name=new_name)

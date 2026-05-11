"""Agent profile domain service."""

from __future__ import annotations

from typing import Protocol

from chariot.models.agent import AgentProfile


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
    ) -> AgentProfile: ...

    async def update_agent_profile(
        self,
        *,
        name: str,
        role: str | None = None,
        prompt_bundle: str | None = None,
        tool_profile: str | None = None,
        provider_profile: str | None = None,
        budget: dict[str, object] | None = None,
        meta: dict[str, object] | None = None,
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
    ) -> AgentProfile:
        return await self._store.create_agent_profile(
            name=name,
            role=role,
            prompt_bundle=prompt_bundle,
            tool_profile=tool_profile,
            provider_profile=provider_profile,
            budget=budget,
            meta=meta,
        )

    async def update_agent(
        self,
        *,
        name: str,
        role: str | None = None,
        prompt_bundle: str | None = None,
        tool_profile: str | None = None,
        provider_profile: str | None = None,
        budget: dict[str, object] | None = None,
        meta: dict[str, object] | None = None,
    ) -> AgentProfile:
        agent = await self._store.get_profile(name)
        if agent is None:
            raise ValueError(f"agent profile {name!r} not found")
        if (
            role is None
            and prompt_bundle is None
            and tool_profile is None
            and provider_profile is None
            and budget is None
            and meta is None
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
        )

    async def delete_agent(self, name: str) -> None:
        agent = await self._store.get_profile(name)
        if agent is None:
            raise ValueError(f"agent profile {name!r} not found")
        await self._store.delete_agent_profile(name)

"""Provider domain service."""

from __future__ import annotations

from typing import Any

from chariot.agent.config import ConfigError
from chariot.models.provider import ProviderEntry
from chariot.repos.auxiliary_repo import AuxiliaryRepo
from chariot.repos.provider_health_repo import ProviderHealthRepo
from chariot.repos.provider_repo import ProviderRepo
from chariot.repos.task_repo import TaskRepo
from chariot.services._session_proxy import SessionRepoProxy


class ProviderService:
    def __init__(self, session_maker: object) -> None:
        self._repo = SessionRepoProxy(session_maker, ProviderRepo)
        self._health = SessionRepoProxy(session_maker, ProviderHealthRepo)
        self._tasks = SessionRepoProxy(session_maker, TaskRepo)
        self._auxiliary = SessionRepoProxy(session_maker, AuxiliaryRepo)

    async def list_entries(self) -> list[ProviderEntry]:
        return await self._repo.list_entries()

    async def list_with_default(self) -> tuple[list[ProviderEntry], ProviderEntry | None]:
        entries = await self._repo.list_entries()
        default = await self._repo.get_default()
        return entries, default

    async def get_entry(self, ref: str) -> ProviderEntry | None:
        return await self._repo.get_entry(ref)

    async def show_with_default(self, ref: str) -> tuple[ProviderEntry, bool] | None:
        entry = await self._repo.get_entry(ref)
        if entry is None:
            return None
        default = await self._repo.get_default()
        is_default = default is not None and default.id == entry.id
        return entry, is_default

    async def get_default(self) -> ProviderEntry | None:
        return await self._repo.get_default()

    async def resolve_provider(self, ref: str) -> ProviderEntry:
        entry = await self._repo.get_entry(ref)
        if entry is None:
            raise ConfigError(f"未知 provider ref: {ref!r}")
        return entry

    async def resolve_provider_id(self, ref: str) -> str:
        return (await self.resolve_provider(ref)).id

    async def status(self) -> dict[str, Any]:
        entries = await self._repo.list_entries()
        default = await self._repo.get_default()
        return {
            "entries": entries,
            "default": default,
            "count": len(entries),
        }

    async def create(
        self,
        *,
        name: str,
        type: str,
        options: dict[str, Any],
        params: dict[str, Any] | None = None,
        slug: str | None = None,
    ) -> ProviderEntry:
        return await self._repo.create(name=name, slug=slug, type=type, options=options, params=params)

    async def update(
        self,
        ref: str,
        *,
        type: str | None = None,
        options: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> ProviderEntry:
        return await self._repo.update(ref, type=type, options=options, params=params)

    async def rename_provider(self, ref: str, new_name: str) -> ProviderEntry:
        return await self._repo.rename(ref, new_name=new_name)

    async def change_provider_slug(self, ref: str, new_slug: str) -> ProviderEntry:
        return await self._repo.change_slug(ref, new_slug=new_slug)

    async def delete(self, ref: str) -> None:
        await self._assert_no_active_references(ref)
        await self._repo.delete(ref)

    async def seed_if_empty(self) -> None:
        await self._repo.seed_if_empty()

    async def set_default(self, ref: str) -> None:
        await self._repo.set_default(ref)

    async def unset_default(self) -> None:
        await self._repo.unset_default()

    async def copy(
        self,
        ref: str,
        *,
        as_name: str | None = None,
        as_slug: str | None = None,
    ) -> ProviderEntry:
        return await self._repo.copy(ref, as_name=as_name, as_slug=as_slug)

    async def list_health_entries(self) -> list[dict[str, Any]]:
        return await self._health.list_entries()

    async def get_health(self, provider_ref: str) -> dict[str, Any] | None:
        provider_id = await self.resolve_provider_id(provider_ref)
        return await self._health.get(provider_id)

    async def record_health_probe(
        self,
        provider_ref: str,
        *,
        ok: bool,
        latency_ms: int,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        entry = await self.resolve_provider(provider_ref)
        return await self._health.record_probe(
            entry.id,
            provider_snapshot=entry.name,
            ok=ok,
            latency_ms=latency_ms,
            error_code=error_code,
            error_message=error_message,
        )

    async def _assert_no_active_references(self, ref: str) -> None:
        entry = await self.resolve_provider(ref)
        blockers: list[str] = []

        default = await self._repo.get_default()
        if default is not None and default.id == entry.id:
            blockers.append("default_provider_id")

        for profile in await self._tasks.list_agent_profiles():
            if profile.provider_id == entry.id:
                blockers.append(f"agent_profiles:{profile.name}")

        for aux in await self._auxiliary.list_entries():
            if aux.provider_id == entry.id:
                blockers.append(f"auxiliary_clients:{aux.name}")

        if blockers:
            joined = ", ".join(blockers)
            raise ConfigError(f"provider {entry.slug!r} 仍被活跃配置引用: {joined}")

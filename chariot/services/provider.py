"""Provider domain service.

Currently a thin wrapper around `ProviderRepo`. Future work will extract
business rules (default management / copy / seed_if_empty etc.) from the
repo into this layer; for now the service exists so callers can depend on
a stable domain-layer entry point.
"""

from __future__ import annotations

from typing import Any

from chariot.models.provider import ProviderEntry
from chariot.repos.provider_health_repo import ProviderHealthRepo
from chariot.repos.provider_repo import ProviderRepo
from chariot.services._session_proxy import SessionRepoProxy


class ProviderService:
    def __init__(self, session_maker: object) -> None:
        self._repo = SessionRepoProxy(session_maker, ProviderRepo)
        self._health = SessionRepoProxy(session_maker, ProviderHealthRepo)

    # ---- read ----

    async def list_entries(self) -> list[ProviderEntry]:
        return await self._repo.list_entries()

    async def list_with_default(self) -> tuple[list[ProviderEntry], ProviderEntry | None]:
        """返回全部 entries + 当前默认 entry。"""
        entries = await self._repo.list_entries()
        default = await self._repo.get_default()
        return entries, default

    async def get_entry(self, name: str) -> ProviderEntry | None:
        return await self._repo.get_entry(name)

    async def show_with_default(self, name: str) -> tuple[ProviderEntry, bool] | None:
        """返回指定 entry + 是否为默认;entry 不存在时返回 None。"""
        entry = await self._repo.get_entry(name)
        if entry is None:
            return None
        default = await self._repo.get_default()
        is_default = default is not None and default.name == entry.name
        return entry, is_default

    async def get_default(self) -> ProviderEntry | None:
        return await self._repo.get_default()

    async def status(self) -> dict[str, Any]:
        """返回 provider 状态概览。"""
        entries = await self._repo.list_entries()
        default = await self._repo.get_default()
        return {
            "entries": entries,
            "default": default,
            "count": len(entries),
        }

    # ---- write ----

    async def create(
        self,
        *,
        name: str,
        type: str,
        options: dict[str, Any],
        params: dict[str, Any] | None = None,
    ) -> ProviderEntry:
        return await self._repo.create(name=name, type=type, options=options, params=params)

    async def update(
        self,
        name: str,
        *,
        type: str | None = None,
        options: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> ProviderEntry:
        return await self._repo.update(name, type=type, options=options, params=params)

    async def delete(self, name: str) -> None:
        await self._repo.delete(name)

    async def set_default(self, name: str) -> None:
        await self._repo.set_default(name)

    async def unset_default(self) -> None:
        await self._repo.unset_default()

    async def copy(self, name: str, *, as_name: str | None = None) -> ProviderEntry:
        return await self._repo.copy(name, as_name=as_name)

    async def list_health_entries(self) -> list[dict[str, Any]]:
        return await self._health.list_entries()

    async def get_health(self, provider_name: str) -> dict[str, Any] | None:
        return await self._health.get(provider_name)

    async def record_health_probe(
        self,
        provider_name: str,
        *,
        ok: bool,
        latency_ms: int,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        return await self._health.record_probe(
            provider_name,
            ok=ok,
            latency_ms=latency_ms,
            error_code=error_code,
            error_message=error_message,
        )

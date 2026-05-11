"""Provider domain service.

Currently a thin wrapper around `ProviderRepo`. Future work will extract
business rules (default management / copy / seed_if_empty etc.) from the
repo into this layer; for now the service exists so callers can depend on
a stable domain-layer entry point.
"""

from __future__ import annotations

from typing import Any

from chariot.models.provider import ProviderEntry
from chariot.repos.provider_repo import ProviderRepo


class ProviderService:
    def __init__(self, repo: ProviderRepo) -> None:
        self._repo = repo

    async def list_entries(self) -> list[ProviderEntry]:
        return await self._repo.list_entries()

    async def get_entry(self, name: str) -> ProviderEntry | None:
        return await self._repo.get_entry(name)

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

    async def get_default(self) -> ProviderEntry | None:
        return await self._repo.get_default()

    async def set_default(self, name: str) -> None:
        await self._repo.set_default(name)

    async def unset_default(self) -> None:
        await self._repo.unset_default()

    async def copy(self, name: str, *, as_name: str | None = None) -> ProviderEntry:
        return await self._repo.copy(name, as_name=as_name)

"""Auxiliary client domain service."""

from __future__ import annotations

from typing import Any

from chariot.models.agent import ClearableStr
from chariot.models.auxiliary import AuxiliaryClientEntry
from chariot.repos.auxiliary_repo import AuxiliaryRepo
from chariot.services._session_proxy import SessionRepoProxy


class AuxiliaryService:
    def __init__(self, session_maker: object) -> None:
        self._repo = SessionRepoProxy(session_maker, AuxiliaryRepo)

    async def list_entries(self) -> list[AuxiliaryClientEntry]:
        return await self._repo.list_entries()

    async def get_entry(self, name: str) -> AuxiliaryClientEntry | None:
        return await self._repo.get_entry(name)

    async def create(
        self,
        *,
        name: str,
        provider_id: str,
        model: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> AuxiliaryClientEntry:
        return await self._repo.create(
            name=name,
            provider_id=provider_id,
            model=model,
            params=params,
        )

    async def update(
        self,
        name: str,
        *,
        provider_id: str | None = None,
        model: ClearableStr = None,
        params: dict[str, Any] | None = None,
    ) -> AuxiliaryClientEntry:
        return await self._repo.update(
            name,
            provider_id=provider_id,
            model=model,
            params=params,
        )

    async def delete(self, name: str) -> None:
        await self._repo.delete(name)

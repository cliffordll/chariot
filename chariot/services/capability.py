"""Capability domain service."""

from __future__ import annotations

from chariot.repos.capability_repo import CapabilityEntry, CapabilityRepo
from chariot.services._session_proxy import SessionRepoProxy


class CapabilityService:
    def __init__(self, session_maker: object) -> None:
        self._repo = SessionRepoProxy(session_maker, CapabilityRepo)

    async def list_entries(self) -> list[CapabilityEntry]:
        return await self._repo.list_entries()

    async def get_entry(self, name: str) -> CapabilityEntry | None:
        return await self._repo.get_entry(name)

    async def set_enabled(self, name: str, enabled: bool) -> CapabilityEntry:
        return await self._repo.set_enabled(name, enabled)

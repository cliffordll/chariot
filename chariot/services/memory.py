"""Memory domain service.

Currently a thin wrapper around `MemoryRepo`. Future work will extract
business rules (auto-capture / retrieval policy / link validation 等)
from the repo into this layer; for now the service exists so callers can
depend on a stable domain-layer entry point.
"""

from __future__ import annotations

from typing import Any

from chariot.memory.policy import MemoryPolicy
from chariot.models.memory import MemoryEntry, MemoryEventEntry, MemoryLinkEntry
from chariot.repos.memory_repo import MemoryRepo
from chariot.services._session_proxy import SessionRepoProxy


class MemoryService:
    def __init__(self, session_maker: object) -> None:
        self._repo = SessionRepoProxy(session_maker, MemoryRepo)

    async def list_entries(
        self,
        *,
        kind: str | None = None,
        pinned: bool | None = None,
        archived: bool | None = False,
        conversation_id: str | None = None,
        provider_name: str | None = None,
        tag: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MemoryEntry]:
        return await self._repo.list_entries(
            kind=kind,
            pinned=pinned,
            archived=archived,
            conversation_id=conversation_id,
            provider_name=provider_name,
            tag=tag,
            search=search,
            limit=limit,
            offset=offset,
        )

    async def get_entry(self, entry_id: str) -> MemoryEntry | None:
        return await self._repo.get_entry(entry_id)

    async def create(
        self,
        *,
        kind: str,
        text: str,
        meta: dict[str, Any] | None = None,
        pinned: bool = False,
        archived: bool = False,
        links: list[dict[str, Any]] | None = None,
    ) -> MemoryEntry:
        return await self._repo.create(
            kind=kind,
            text=text,
            meta=meta,
            pinned=pinned,
            archived=archived,
            links=links,
        )

    async def update(
        self,
        entry_id: str,
        *,
        kind: str | None = None,
        text: str | None = None,
        meta: dict[str, Any] | None = None,
        pinned: bool | None = None,
        archived: bool | None = None,
        links: list[dict[str, Any]] | None = None,
    ) -> MemoryEntry:
        return await self._repo.update(
            entry_id,
            kind=kind,
            text=text,
            meta=meta,
            pinned=pinned,
            archived=archived,
            links=links,
        )

    async def delete(self, entry_id: str) -> None:
        await self._repo.delete(entry_id)

    async def pin(self, entry_id: str, pinned: bool = True) -> MemoryEntry:
        return await self._repo.pin(entry_id, pinned=pinned)

    async def archive(self, entry_id: str, archived: bool = True) -> MemoryEntry:
        return await self._repo.archive(entry_id, archived=archived)

    async def list_events(
        self,
        *,
        memory_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MemoryEventEntry]:
        return await self._repo.list_events(memory_id=memory_id, limit=limit, offset=offset)

    async def list_links(
        self,
        *,
        memory_id: str | None = None,
        link_type: str | None = None,
        link_value: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MemoryLinkEntry]:
        return await self._repo.list_links(
            memory_id=memory_id,
            link_type=link_type,
            link_value=link_value,
            limit=limit,
            offset=offset,
        )

    async def search_entries(self, query: str, *, limit: int = 50, offset: int = 0) -> list[MemoryEntry]:
        return await self._repo.search_entries(query, limit=limit, offset=offset)

    async def list_relevant_entries(
        self,
        *,
        conversation_id: str | None = None,
        provider_name: str | None = None,
        tags: list[str] | None = None,
        limit: int = 8,
        policy: MemoryPolicy | None = None,
    ) -> list[MemoryEntry]:
        return await self._repo.list_relevant_entries(
            conversation_id=conversation_id,
            provider_name=provider_name,
            tags=tags,
            limit=limit,
            policy=policy,
        )

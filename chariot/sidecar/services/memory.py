"""Memory service for sidecar memory methods."""

from __future__ import annotations

from typing import Any

from chariot.rpc.jsonrpc import JsonRpcServer, RpcError
from chariot.repos.memory_repo import MemoryRepo
from chariot.sidecar.runtime import SidecarRuntime


class MemoryService:
    def __init__(self, runtime: SidecarRuntime) -> None:
        self._runtime = runtime

    async def list_entries(
        self,
        session: Any,
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
    ) -> list[dict[str, Any]]:
        entries = await MemoryRepo(session).list_entries(
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
        return [self._entry_to_dict(entry) for entry in entries]

    async def get_entry(self, session: Any, *, memory_id: str) -> dict[str, Any]:
        entry = await MemoryRepo(session).get_entry(memory_id)
        if entry is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"memory {memory_id!r} not found")
        return self._entry_to_dict(entry)

    async def create_entry(
        self,
        session: Any,
        *,
        kind: str,
        text: str,
        meta: dict[str, Any] | None = None,
        pinned: bool = False,
        archived: bool = False,
        links: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        entry = await MemoryRepo(session).create(
            kind=kind,
            text=text,
            meta=meta,
            pinned=pinned,
            archived=archived,
            links=links,
        )
        return self._entry_to_dict(entry)

    async def update_entry(
        self,
        session: Any,
        *,
        memory_id: str,
        kind: str | None = None,
        text: str | None = None,
        meta: dict[str, Any] | None = None,
        pinned: bool | None = None,
        archived: bool | None = None,
        links: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        entry = await MemoryRepo(session).update(
            memory_id,
            kind=kind,
            text=text,
            meta=meta,
            pinned=pinned,
            archived=archived,
            links=links,
        )
        return self._entry_to_dict(entry)

    async def delete_entry(self, session: Any, *, memory_id: str) -> dict[str, Any]:
        await MemoryRepo(session).delete(memory_id)
        return {"deleted": memory_id}

    async def pin_entry(self, session: Any, *, memory_id: str, pinned: bool = True) -> dict[str, Any]:
        entry = await MemoryRepo(session).pin(memory_id, pinned=pinned)
        return self._entry_to_dict(entry)

    async def archive_entry(
        self,
        session: Any,
        *,
        memory_id: str,
        archived: bool = True,
    ) -> dict[str, Any]:
        entry = await MemoryRepo(session).archive(memory_id, archived=archived)
        return self._entry_to_dict(entry)

    async def list_events(self, session: Any, *, memory_id: str | None = None) -> list[dict[str, Any]]:
        entries = await MemoryRepo(session).list_events(memory_id=memory_id)
        return [self._event_to_dict(entry) for entry in entries]

    async def list_links(
        self,
        session: Any,
        *,
        memory_id: str | None = None,
        link_type: str | None = None,
        link_value: str | None = None,
    ) -> list[dict[str, Any]]:
        entries = await MemoryRepo(session).list_links(
            memory_id=memory_id,
            link_type=link_type,
            link_value=link_value,
        )
        return [self._link_to_dict(entry) for entry in entries]

    async def search_entries(self, session: Any, *, query: str) -> list[dict[str, Any]]:
        entries = await MemoryRepo(session).search_entries(query)
        return [self._entry_to_dict(entry) for entry in entries]

    async def list_relevant_entries(
        self,
        session: Any,
        *,
        conversation_id: str | None = None,
        provider_name: str | None = None,
        tags: list[str] | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        entries = await MemoryRepo(session).list_relevant_entries(
            conversation_id=conversation_id,
            provider_name=provider_name,
            tags=tags,
            limit=limit,
        )
        return [self._entry_to_dict(entry) for entry in entries]

    @staticmethod
    def _entry_to_dict(entry) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        return {
            "id": entry.id,
            "kind": entry.kind,
            "text": entry.text,
            "meta": entry.meta,
            "pinned": entry.pinned,
            "archived": entry.archived,
            "created_at": entry.created_at.isoformat(),
            "updated_at": entry.updated_at.isoformat(),
        }

    @staticmethod
    def _event_to_dict(entry) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        return {
            "id": entry.id,
            "memory_id": entry.memory_id,
            "event_type": entry.event_type,
            "payload": entry.payload,
            "created_at": entry.created_at.isoformat(),
        }

    @staticmethod
    def _link_to_dict(entry) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        return {
            "id": entry.id,
            "memory_id": entry.memory_id,
            "link_type": entry.link_type,
            "link_value": entry.link_value,
            "created_at": entry.created_at.isoformat(),
        }

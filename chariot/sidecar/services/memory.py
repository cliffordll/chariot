"""Memory API surface for sidecar admin methods."""

from __future__ import annotations

from typing import Any

from chariot.models.memory import MemoryEntry, MemoryEventEntry, MemoryLinkEntry
from chariot.rpc.jsonrpc import JsonRpcServer, RpcError
from chariot.services.memory import MemoryService
from chariot.sidecar.runtime import SidecarRuntime


class MemoryApi:
    def __init__(self, runtime: SidecarRuntime) -> None:
        self._runtime = runtime

    async def list_entries(
        self,
        *,
        kind: str | None = None,
        pinned: bool | None = None,
        archived: bool | None = False,
        conversation_id: str | None = None,
        provider_snapshot: str | None = None,
        tag: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        entries = await MemoryService(self._runtime).list_entries(
            kind=kind,
            pinned=pinned,
            archived=archived,
            conversation_id=conversation_id,
            provider_snapshot=provider_snapshot,
            tag=tag,
            search=search,
            limit=limit,
            offset=offset,
        )
        return [self._entry_to_dict(entry) for entry in entries]

    async def get_entry(self, *, memory_id: str) -> dict[str, Any]:
        entry = await MemoryService(self._runtime).get_entry(memory_id)
        if entry is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"memory {memory_id!r} not found")
        return self._entry_to_dict(entry)

    async def create_entry(
        self,
        *,
        kind: str,
        text: str,
        meta: dict[str, Any] | None = None,
        pinned: bool = False,
        archived: bool = False,
        links: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        entry = await MemoryService(self._runtime).create(
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
        *,
        memory_id: str,
        kind: str | None = None,
        text: str | None = None,
        meta: dict[str, Any] | None = None,
        pinned: bool | None = None,
        archived: bool | None = None,
        links: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        entry = await MemoryService(self._runtime).update(
            memory_id,
            kind=kind,
            text=text,
            meta=meta,
            pinned=pinned,
            archived=archived,
            links=links,
        )
        return self._entry_to_dict(entry)

    async def delete_entry(self, *, memory_id: str) -> dict[str, Any]:
        await MemoryService(self._runtime).delete(memory_id)
        return {"deleted": memory_id}

    async def pin_entry(self, *, memory_id: str, pinned: bool = True) -> dict[str, Any]:
        entry = await MemoryService(self._runtime).pin(memory_id, pinned=pinned)
        return self._entry_to_dict(entry)

    async def archive_entry(
        self,
        *,
        memory_id: str,
        archived: bool = True,
    ) -> dict[str, Any]:
        entry = await MemoryService(self._runtime).archive(memory_id, archived=archived)
        return self._entry_to_dict(entry)

    async def list_events(self, *, memory_id: str | None = None) -> list[dict[str, Any]]:
        entries = await MemoryService(self._runtime).list_events(memory_id=memory_id)
        return [self._event_to_dict(entry) for entry in entries]

    async def list_links(
        self,
        *,
        memory_id: str | None = None,
        link_type: str | None = None,
        link_value: str | None = None,
    ) -> list[dict[str, Any]]:
        entries = await MemoryService(self._runtime).list_links(
            memory_id=memory_id,
            link_type=link_type,
            link_value=link_value,
        )
        return [self._link_to_dict(entry) for entry in entries]

    async def search_entries(self, *, query: str) -> list[dict[str, Any]]:
        entries = await MemoryService(self._runtime).search_entries(query)
        return [self._entry_to_dict(entry) for entry in entries]

    async def list_relevant_entries(
        self,
        *,
        conversation_id: str | None = None,
        provider_snapshot: str | None = None,
        tags: list[str] | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        entries = await MemoryService(self._runtime).list_relevant_entries(
            conversation_id=conversation_id,
            provider_snapshot=provider_snapshot,
            tags=tags,
            limit=limit,
        )
        return [self._entry_to_dict(entry) for entry in entries]

    @staticmethod
    def _entry_to_dict(entry: MemoryEntry) -> dict[str, Any]:
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
    def _event_to_dict(entry: MemoryEventEntry) -> dict[str, Any]:
        return {
            "id": entry.id,
            "memory_id": entry.memory_id,
            "event_type": entry.event_type,
            "payload": entry.payload,
            "created_at": entry.created_at.isoformat(),
        }

    @staticmethod
    def _link_to_dict(entry: MemoryLinkEntry) -> dict[str, Any]:
        return {
            "id": entry.id,
            "memory_id": entry.memory_id,
            "link_type": entry.link_type,
            "link_value": entry.link_value,
            "created_at": entry.created_at.isoformat(),
        }

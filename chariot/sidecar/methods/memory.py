"""sidecar memory RPC adapters."""

from __future__ import annotations

from typing import Any

from chariot.rpc.jsonrpc import RpcContext
from chariot.sidecar.methods import MethodBase
from chariot.sidecar.runtime import SidecarRuntime
from chariot.sidecar.services import MemoryApi


class MemoryMethods(MethodBase):
    def __init__(self, runtime: SidecarRuntime) -> None:
        super().__init__(runtime)
        self._service = MemoryApi(runtime)

    async def list_(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        kind = self._optional_str(params, "kind")
        conversation_id = self._optional_str(params, "conversation_id")
        provider_name = self._optional_str(params, "provider_name")
        tag = self._optional_str(params, "tag")
        search = self._optional_str(params, "search")
        pinned = self._optional_bool(params, "pinned")
        archived = self._optional_bool(params, "archived")
        limit = int(params.get("limit", 50))
        offset = int(params.get("offset", 0))
        async with self._session() as session:
            entries = await self._service.list_entries(
                session,
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
        return {"entries": entries}

    async def show(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        memory_id = self._require_str(params, "memory_id")
        async with self._session() as session:
            entry = await self._service.get_entry(session, memory_id=memory_id)
        return {"memory": entry}

    async def add(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        kind = self._require_str(params, "kind")
        text = self._require_str(params, "text")
        meta = self._optional_dict(params, "meta")
        pinned = bool(params.get("pinned", False))
        archived = bool(params.get("archived", False))
        links = self._optional_list_of_dicts(params, "links")
        async with self._session() as session:
            entry = await self._service.create_entry(
                session,
                kind=kind,
                text=text,
                meta=meta,
                pinned=pinned,
                archived=archived,
                links=links,
            )
        await self.agent.audit_hooks.record_memory_store(
            memory_id=entry["id"],
            action="create",
            kind=entry.get("kind"),
            pinned=entry.get("pinned"),
        )
        return {"memory": entry}

    async def update(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        memory_id = self._require_str(params, "memory_id")
        kind = self._optional_str(params, "kind")
        text = self._optional_str(params, "text")
        meta = self._optional_dict(params, "meta")
        pinned = params.get("pinned")
        archived = params.get("archived")
        if pinned is not None and not isinstance(pinned, bool):
            raise TypeError("pinned must be a boolean or null")
        if archived is not None and not isinstance(archived, bool):
            raise TypeError("archived must be a boolean or null")
        links = self._optional_list_of_dicts(params, "links")
        async with self._session() as session:
            entry = await self._service.update_entry(
                session,
                memory_id=memory_id,
                kind=kind,
                text=text,
                meta=meta,
                pinned=pinned,
                archived=archived,
                links=links,
            )
        await self.agent.audit_hooks.record_memory_store(
            memory_id=entry["id"],
            action="update",
            kind=entry.get("kind"),
            pinned=entry.get("pinned"),
        )
        return {"memory": entry}

    async def delete(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        memory_id = self._require_str(params, "memory_id")
        async with self._session() as session:
            result = await self._service.delete_entry(session, memory_id=memory_id)
        await self.agent.audit_hooks.record_memory_store(
            memory_id=memory_id,
            action="delete",
        )
        return result

    async def pin(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        memory_id = self._require_str(params, "memory_id")
        pinned = bool(params.get("pinned", True))
        async with self._session() as session:
            entry = await self._service.pin_entry(session, memory_id=memory_id, pinned=pinned)
        await self.agent.audit_hooks.record_memory_store(
            memory_id=entry["id"],
            action="pin" if pinned else "unpin",
            kind=entry.get("kind"),
            pinned=entry.get("pinned"),
        )
        return {"memory": entry}

    async def archive(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        memory_id = self._require_str(params, "memory_id")
        archived = bool(params.get("archived", True))
        async with self._session() as session:
            entry = await self._service.archive_entry(session, memory_id=memory_id, archived=archived)
        await self.agent.audit_hooks.record_memory_store(
            memory_id=entry["id"],
            action="archive" if archived else "restore",
            kind=entry.get("kind"),
            pinned=entry.get("pinned"),
        )
        return {"memory": entry}

    async def events(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        memory_id = self._optional_str(params, "memory_id")
        async with self._session() as session:
            entries = await self._service.list_events(session, memory_id=memory_id)
        return {"events": entries}

    async def links(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        memory_id = self._optional_str(params, "memory_id")
        link_type = self._optional_str(params, "link_type")
        link_value = self._optional_str(params, "link_value")
        async with self._session() as session:
            entries = await self._service.list_links(
                session,
                memory_id=memory_id,
                link_type=link_type,
                link_value=link_value,
            )
        return {"links": entries}

    async def search(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        query = self._require_str(params, "query")
        async with self._session() as session:
            entries = await self._service.search_entries(session, query=query)
        return {"entries": entries}

    @staticmethod
    def _optional_bool(params: dict[str, Any], key: str) -> bool | None:
        val = params.get(key)
        if val is None:
            return None
        if not isinstance(val, bool):
            raise TypeError(f"{key} must be a boolean or null")
        return val

    @staticmethod
    def _optional_list_of_dicts(params: dict[str, Any], key: str) -> list[dict[str, Any]] | None:
        val = params.get(key)
        if val is None:
            return None
        if not isinstance(val, list):
            raise TypeError(f"{key} must be a list or null")
        out: list[dict[str, Any]] = []
        for idx, item in enumerate(val):
            if not isinstance(item, dict):
                raise TypeError(f"{key}[{idx}] must be an object")
            out.append(item)
        return out

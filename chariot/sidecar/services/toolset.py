"""Toolset API surface for sidecar admin methods."""

from __future__ import annotations

from typing import Any

from chariot.models.toolset import Toolset
from chariot.rpc.jsonrpc import JsonRpcServer, RpcError
from chariot.services.toolset import ToolsetService
from chariot.sidecar.runtime import SidecarRuntime


class ToolsetApi:
    def __init__(self, runtime: SidecarRuntime) -> None:
        self._runtime = runtime

    async def list_entries(self) -> list[dict[str, Any]]:
        service = ToolsetService(self._runtime)
        entries = await service.list_entries()
        return [self.serialize(entry) for entry in entries]

    async def get_entry(self, *, name: str) -> dict[str, Any]:
        service = ToolsetService(self._runtime)
        entry = await service.get_entry(name)
        if entry is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"toolset {name!r} not found")
        return self.serialize(entry)

    async def create(
        self,
        *,
        name: str,
        description: str | None = None,
        members: list[str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        service = ToolsetService(self._runtime)
        entry = await service.create(
            name=name,
            description=description,
            members=members,
            meta=meta,
        )
        return self.serialize(entry)

    async def update(
        self,
        *,
        name: str,
        description: str | None = None,
        members: list[str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        service = ToolsetService(self._runtime)
        entry = await service.update(
            name,
            description=description,
            members=members,
            meta=meta,
        )
        return self.serialize(entry)

    async def delete(self, *, name: str) -> str:
        service = ToolsetService(self._runtime)
        await service.delete(name)
        return name

    async def add_member(self, *, name: str, tool_name: str) -> dict[str, Any]:
        service = ToolsetService(self._runtime)
        entry = await service.add_member(name, tool_name)
        return self.serialize(entry)

    async def remove_member(self, *, name: str, tool_name: str) -> dict[str, Any]:
        service = ToolsetService(self._runtime)
        entry = await service.remove_member(name, tool_name)
        return self.serialize(entry)

    @staticmethod
    def serialize(entry: Toolset) -> dict[str, Any]:
        return {
            "id": entry.id,
            "name": entry.name,
            "description": entry.description,
            "members": list(entry.members),
            "meta": entry.meta,
            "created_at": entry.created_at.isoformat() if entry.created_at is not None else None,
            "updated_at": entry.updated_at.isoformat() if entry.updated_at is not None else None,
        }

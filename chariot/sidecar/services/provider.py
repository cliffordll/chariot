"""Provider API surface for sidecar admin methods."""

from __future__ import annotations

from typing import Any

from chariot.models.provider import ProviderEntry
from chariot.providers.prober import ProviderProber
from chariot.providers.registry import ProviderRegistry
from chariot.rpc.jsonrpc import JsonRpcServer, RpcError
from chariot.services.provider import ProviderService
from chariot.sidecar.runtime import SidecarRuntime


class ProviderApi:
    def __init__(self, runtime: SidecarRuntime) -> None:
        self._runtime = runtime

    async def list_entries(self) -> list[dict[str, Any]]:
        service = ProviderService(self._runtime)
        entries, default = await service.list_with_default()
        default_id = default.id if default else None
        health_map = {row["provider_id"]: row for row in await service.list_health_entries()}
        return [
            {
                **self.serialize(entry, health=health_map.get(entry.id)),
                "default": entry.id == default_id,
            }
            for entry in entries
        ]

    async def show_entry(self, *, name: str) -> dict[str, Any]:
        service = ProviderService(self._runtime)
        result = await service.show_with_default(name)
        if result is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"provider {name!r} not found")
        entry, is_default = result
        health = await service.get_health(entry.id)
        return {
            **self.serialize(entry, health=health),
            "default": is_default,
        }

    async def status(self) -> dict[str, Any]:
        service = ProviderService(self._runtime)
        result = await service.status()
        entries = result["entries"]
        default = result["default"]
        health_map = {row["provider_id"]: row for row in await service.list_health_entries()}
        return {
            "default_provider": f"{default.name} ({default.slug})" if default is not None else None,
            "provider_count": len(entries),
            "known_types": sorted(ProviderRegistry.known_types()),
            "providers": [
                {
                    **self.serialize(entry, health=health_map.get(entry.id)),
                    "default": default is not None and entry.id == default.id,
                }
                for entry in entries
            ],
        }

    async def add_entry(
        self,
        *,
        name: str,
        type_: str,
        options: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        entry = await ProviderService(self._runtime).create(
            name=name,
            type=type_,
            options=options,
            params=params,
        )
        await self._runtime.reload()
        return self.serialize(entry)

    async def update_entry(
        self,
        *,
        name: str,
        type_: str | None,
        options: dict[str, Any] | None,
        params: dict[str, Any] | None,
    ) -> dict[str, Any]:
        entry = await ProviderService(self._runtime).update(
            name,
            type=type_,
            options=options,
            params=params,
        )
        await self._runtime.reload()
        return self.serialize(entry)

    async def delete_entry(self, *, name: str) -> str:
        service = ProviderService(self._runtime)
        entry = await service.get_entry(name)
        if entry is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"provider {name!r} not found")
        await service.delete(name)
        await self._runtime.reload()
        return name

    async def set_default_entry(self, *, name: str) -> dict[str, Any]:
        service = ProviderService(self._runtime)
        await service.set_default(name)
        await self._runtime.reload()
        result = await service.show_with_default(name)
        if result is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"provider {name!r} not found")
        entry, is_default = result
        return {
            **self.serialize(entry),
            "default": is_default,
        }

    async def probe(self, *, name: str) -> dict[str, Any]:
        service = ProviderService(self._runtime)
        entry = await service.get_entry(name)
        if entry is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"provider {name!r} not found")
        result = await ProviderProber.probe(entry)
        await service.record_health_probe(
            entry.id,
            ok=result.ok,
            latency_ms=result.latency_ms,
            error_code=result.error.code if result.error is not None else None,
            error_message=result.error.message if result.error is not None else None,
        )
        return {
            "ok": result.ok,
            "latency_ms": result.latency_ms,
            "error": (
                {"code": result.error.code, "message": result.error.message} if result.error is not None else None
            ),
        }

    @staticmethod
    def serialize(entry: ProviderEntry, *, health: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "id": entry.id,
            "slug": entry.slug,
            "name": entry.name,
            "type": entry.type,
            "options": entry.options,
            "params": entry.params,
            "capabilities": ProviderRegistry.capabilities_for(entry.type),
            "health": health,
        }

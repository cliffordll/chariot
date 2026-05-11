"""Provider service for sidecar admin methods."""

from __future__ import annotations

from typing import Any

from chariot.agent.config import ProviderEntry
from chariot.providers.prober import ProviderProber
from chariot.providers.registry import ProviderRegistry
from chariot.repos.provider_health_repo import ProviderHealthRepo
from chariot.repos.provider_repo import ProviderRepo
from chariot.rpc.jsonrpc import JsonRpcServer, RpcError
from chariot.sidecar.runtime import SidecarRuntime


class ProviderService:
    def __init__(self, runtime: SidecarRuntime) -> None:
        self._runtime = runtime

    async def list_entries(self, session: Any) -> list[dict[str, Any]]:
        repo = ProviderRepo(session)
        health_repo = ProviderHealthRepo(session)
        entries = await repo.list_entries()
        default = await repo.get_default()
        default_name = default.name if default else None
        health_map = {row["provider_name"]: row for row in await health_repo.list_entries()}
        return [
            {
                **self.serialize(entry, health=health_map.get(entry.name)),
                "default": entry.name == default_name,
            }
            for entry in entries
        ]

    async def show_entry(self, session: Any, *, name: str) -> dict[str, Any]:
        repo = ProviderRepo(session)
        health_repo = ProviderHealthRepo(session)
        entry = await repo.get_entry(name)
        if entry is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"provider {name!r} not found")
        default = await repo.get_default()
        health = await health_repo.get(entry.name)
        return {
            **self.serialize(entry, health=health),
            "default": default is not None and default.name == entry.name,
        }

    async def status(self, session: Any) -> dict[str, Any]:
        repo = ProviderRepo(session)
        health_repo = ProviderHealthRepo(session)
        entries = await repo.list_entries()
        default = await repo.get_default()
        health_map = {row["provider_name"]: row for row in await health_repo.list_entries()}
        return {
            "default_provider": default.name if default is not None else None,
            "provider_count": len(entries),
            "known_types": sorted(ProviderRegistry.known_types()),
            "providers": [
                {
                    **self.serialize(entry, health=health_map.get(entry.name)),
                    "default": default is not None and entry.name == default.name,
                }
                for entry in entries
            ],
        }

    async def add_entry(
        self,
        session: Any,
        *,
        name: str,
        type_: str,
        options: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        entry = await ProviderRepo(session).create(
            name=name,
            type=type_,
            options=options,
            params=params,
        )
        await self._runtime.reload()
        return self.serialize(entry)

    async def update_entry(
        self,
        session: Any,
        *,
        name: str,
        type_: str | None,
        options: dict[str, Any] | None,
        params: dict[str, Any] | None,
    ) -> dict[str, Any]:
        entry = await ProviderRepo(session).update(
            name,
            type=type_,
            options=options,
            params=params,
        )
        await self._runtime.reload()
        return self.serialize(entry)

    async def delete_entry(self, session: Any, *, name: str) -> str:
        await ProviderRepo(session).delete(name)
        await self._runtime.reload()
        return name

    async def set_default_entry(self, session: Any, *, name: str) -> dict[str, Any]:
        repo = ProviderRepo(session)
        await repo.set_default(name)
        await self._runtime.reload()
        entry = await repo.get_entry(name)
        if entry is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"provider {name!r} not found")
        default = await repo.get_default()
        return {
            **self.serialize(entry),
            "default": default is not None and default.name == entry.name,
        }

    async def probe(self, session: Any, *, name: str) -> dict[str, Any]:
        entry = await ProviderRepo(session).get_entry(name)
        if entry is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"provider {name!r} not found")
        result = await ProviderProber.probe(entry)
        await ProviderHealthRepo(session).record_probe(
            entry.name,
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
            "name": entry.name,
            "type": entry.type,
            "options": entry.options,
            "params": entry.params,
            "capabilities": ProviderRegistry.capabilities_for(entry.type),
            "health": health,
        }

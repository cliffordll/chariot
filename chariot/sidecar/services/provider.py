"""Provider service for sidecar admin methods."""

from __future__ import annotations

from typing import Any

from chariot.agent.config import ProviderEntry
from chariot.providers.prober import ProviderProber
from chariot.repos.provider_repo import ProviderRepo
from chariot.rpc.jsonrpc import JsonRpcServer, RpcError
from chariot.sidecar.runtime import SidecarRuntime


class ProviderService:
    def __init__(self, runtime: SidecarRuntime) -> None:
        self._runtime = runtime

    async def list_entries(self, session: Any) -> list[dict[str, Any]]:
        repo = ProviderRepo(session)
        entries = await repo.list_entries()
        default = await repo.get_default()
        default_name = default.name if default else None
        return [{**self.serialize(entry), "default": entry.name == default_name} for entry in entries]

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

    async def probe(self, session: Any, *, name: str) -> dict[str, Any]:
        entry = await ProviderRepo(session).get_entry(name)
        if entry is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"provider {name!r} not found")
        result = await ProviderProber.probe(entry)
        return {
            "ok": result.ok,
            "latency_ms": result.latency_ms,
            "error": (
                {"code": result.error.code, "message": result.error.message}
                if result.error is not None
                else None
            ),
        }

    @staticmethod
    def serialize(entry: ProviderEntry) -> dict[str, Any]:
        return {
            "name": entry.name,
            "type": entry.type,
            "options": entry.options,
            "params": entry.params,
        }

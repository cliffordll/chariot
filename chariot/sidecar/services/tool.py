"""Tool API surface for sidecar admin methods."""

from __future__ import annotations

import time
from typing import Any

from chariot.agent.exceptions import ConfigError
from chariot.models.tool import ToolEntry
from chariot.rpc.jsonrpc import JsonRpcServer, RpcError
from chariot.services.tool import ToolService
from chariot.sidecar.runtime import SidecarRuntime
from chariot.tools.registry import ToolRegistry


class ToolApi:
    def __init__(self, runtime: SidecarRuntime) -> None:
        self._runtime = runtime

    async def list_entries(self) -> list[dict[str, Any]]:
        entries = await ToolService(self._runtime).list_entries()
        return [self.serialize(entry) for entry in entries]

    async def get_entry(self, *, name: str) -> dict[str, Any]:
        entry = await ToolService(self._runtime).get_entry(name)
        if entry is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"tool {name!r} not found")
        return self.serialize(entry)

    async def probe_entry(self, *, name: str) -> dict[str, Any]:
        entry = await ToolService(self._runtime).get_entry(name)
        if entry is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"tool {name!r} not found")
        start = time.perf_counter()
        try:
            tool = ToolRegistry.build(entry)
            _ = tool.schema()
        except (ConfigError, ValueError, TypeError) as e:
            latency_ms = int((time.perf_counter() - start) * 1000)
            return {
                "ok": False,
                "latency_ms": latency_ms,
                "error": {"code": "invalid_tool_config", "message": str(e)},
            }
        latency_ms = int((time.perf_counter() - start) * 1000)
        return {"ok": True, "latency_ms": latency_ms, "error": None}

    async def set_enabled(self, *, name: str, enabled: bool) -> dict[str, Any]:
        entry = await ToolService(self._runtime).update(name, enabled=enabled)
        await self._runtime.reload()
        return self.serialize(entry)

    async def update_options(self, *, name: str, options: dict[str, Any]) -> dict[str, Any]:
        entry = await ToolService(self._runtime).update(name, options=options)
        await self._runtime.reload()
        return self.serialize(entry)

    async def add(
        self,
        *,
        name: str,
        custom_type: str,
        options: dict[str, Any],
        description: str = "",
    ) -> dict[str, Any]:
        entry = await ToolService(self._runtime).create(
            name=name,
            type=custom_type,
            enabled=True,
            options=options,
            source="custom",
            description=description,
            custom_type=custom_type,
        )
        await self._runtime.reload()
        return self.serialize(entry)

    async def delete(self, *, name: str) -> dict[str, Any]:
        await ToolService(self._runtime).delete(name)
        await self._runtime.reload()
        return {"deleted": name}

    async def update(
        self,
        *,
        name: str,
        options: dict[str, Any] | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        entry = await ToolService(self._runtime).update_full(
            name,
            options=options,
            description=description,
        )
        await self._runtime.reload()
        return self.serialize(entry)

    @staticmethod
    def serialize(entry: ToolEntry) -> dict[str, Any]:
        schema: dict[str, Any] | None
        try:
            schema = ToolRegistry.build(entry).schema()
        except (ConfigError, ValueError, TypeError):
            schema = None
        return {
            "name": entry.name,
            "type": entry.type,
            "enabled": entry.enabled,
            "options": entry.options,
            "source": entry.source,
            "description": entry.description,
            "custom_type": entry.custom_type,
            "schema_": schema,
        }

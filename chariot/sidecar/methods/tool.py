"""sidecar tool RPC adapters."""

from __future__ import annotations

from typing import Any

from chariot.rpc.jsonrpc import RpcContext
from chariot.sidecar.methods import MethodBase
from chariot.sidecar.runtime import SidecarRuntime
from chariot.sidecar.services import ToolApi


class ToolMethods(MethodBase):
    def __init__(self, runtime: SidecarRuntime) -> None:
        super().__init__(runtime)
        self._service = ToolApi(runtime)

    async def list_(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del params, ctx
        async with self._session() as session:
            tools = await self._service.list_entries(session)
        return {"tools": tools}

    async def show(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        async with self._session() as session:
            tool = await self._service.get_entry(session, name=name)
        return {"tool": tool}

    async def enable(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        return await self._set_enabled(params, enabled=True)

    async def disable(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        return await self._set_enabled(params, enabled=False)

    async def _set_enabled(self, params: dict[str, Any], *, enabled: bool) -> dict[str, Any]:
        name = self._require_str(params, "name")
        async with self._session() as session:
            tool = await self._service.set_enabled(session, name=name, enabled=enabled)
        return {"tool": tool}

    async def config(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        options = self._require_dict(params, "options")
        async with self._session() as session:
            tool = await self._service.update_options(session, name=name, options=options)
        return {"tool": tool}

    async def probe(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        async with self._session() as session:
            return await self._service.probe_entry(session, name=name)

    async def create_custom_tool(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        custom_type = self._require_str(params, "custom_type")
        options = self._require_dict(params, "options")
        description = self._optional_str(params, "description") or ""
        async with self._session() as session:
            tool = await self._service.create_custom_tool(
                session,
                name=name,
                custom_type=custom_type,
                options=options,
                description=description,
            )
        return {"tool": tool}

    async def delete_custom_tool(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        async with self._session() as session:
            result = await self._service.delete_custom_tool(session, name=name)
        return result

    async def update_custom_tool(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        options = self._optional_dict(params, "options")
        description = self._optional_str(params, "description")
        async with self._session() as session:
            tool = await self._service.update_custom_tool(
                session,
                name=name,
                options=options,
                description=description,
            )
        return {"tool": tool}

"""sidecar toolset RPC adapters."""

from __future__ import annotations

from typing import Any, cast

from chariot.rpc.jsonrpc import JsonRpcServer, RpcContext, RpcError
from chariot.sidecar.methods import MethodBase
from chariot.sidecar.runtime import SidecarRuntime
from chariot.sidecar.services import ToolsetApi


class ToolsetMethods(MethodBase):
    def __init__(self, runtime: SidecarRuntime) -> None:
        super().__init__(runtime)
        self._service = ToolsetApi(runtime)

    async def list_(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del params, ctx
        async with self._rpc_errors():
            entries = await self._service.list_entries()
        return {"toolsets": entries}

    async def show(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        async with self._rpc_errors():
            entry = await self._service.get_entry(name=name)
        return {"toolset": entry}

    async def add(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        description = self._optional_str(params, "description")
        members = self._optional_str_list(params, "members")
        meta = self._optional_dict(params, "meta")
        async with self._rpc_errors():
            entry = await self._service.create(
                name=name,
                description=description,
                members=members,
                meta=meta,
            )
        return {"toolset": entry}

    async def update(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        rename = self._optional_str(params, "rename")
        description = self._optional_str(params, "description")
        members = self._optional_str_list(params, "members")
        meta = self._optional_dict(params, "meta")
        async with self._rpc_errors():
            entry = await self._service.update(
                name=name,
                rename=rename,
                description=description,
                members=members,
                meta=meta,
            )
        return {"toolset": entry}

    async def delete(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        async with self._rpc_errors():
            deleted = await self._service.delete(name=name)
        return {"deleted": deleted}

    async def add_member(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        tool_name = self._require_str(params, "tool_name")
        async with self._rpc_errors():
            entry = await self._service.add_member(name=name, tool_name=tool_name)
        return {"toolset": entry}

    async def remove_member(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        tool_name = self._require_str(params, "tool_name")
        async with self._rpc_errors():
            entry = await self._service.remove_member(name=name, tool_name=tool_name)
        return {"toolset": entry}

    @staticmethod
    def _optional_str_list(params: dict[str, Any], key: str) -> list[str] | None:
        val = params.get(key)
        if val is None:
            return None
        if not isinstance(val, list):
            raise RpcError(JsonRpcServer.ERR_INVALID_PARAMS, f"{key!r} must be a list of strings or null")
        out: list[str] = []
        for idx, item in enumerate(cast(list[Any], val)):
            if not isinstance(item, str):
                raise RpcError(JsonRpcServer.ERR_INVALID_PARAMS, f"{key}[{idx}] must be a string")
            out.append(item)
        return out

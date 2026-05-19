"""sidecar context RPC adapters."""

from __future__ import annotations

from typing import Any

from chariot.rpc.jsonrpc import RpcContext
from chariot.sidecar.methods import MethodBase
from chariot.sidecar.runtime import SidecarRuntime
from chariot.sidecar.services import ContextApi


class ContextMethods(MethodBase):
    def __init__(self, runtime: SidecarRuntime) -> None:
        super().__init__(runtime)
        self._service = ContextApi(runtime)

    async def bundles(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del params, ctx
        async with self._rpc_errors():
            bundles = await self._service.list_bundles()
        return {"bundles": bundles}

    async def show(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        async with self._rpc_errors():
            bundle = await self._service.get_bundle(name=name)
        return {"bundle": bundle}

    async def versions(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        bundle_name = self._require_str(params, "bundle_name")
        async with self._rpc_errors():
            versions = await self._service.list_versions(bundle_name=bundle_name)
        return {"versions": versions}

    async def version(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        bundle_name = self._require_str(params, "bundle_name")
        version = self._require_str(params, "version")
        async with self._rpc_errors():
            entry = await self._service.get_version(bundle_name=bundle_name, version=version)
        return {"version": entry}

    async def add(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        description = self._optional_str(params, "description")
        spec = self._optional_dict(params, "spec")
        async with self._rpc_errors():
            result = await self._service.add_bundle(name=name, description=description, spec=spec)
        return result

    async def update(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        rename = self._optional_str(params, "rename")
        description = params.get("description")
        if description is not None and not isinstance(description, str):
            raise TypeError("description must be a string or null")
        spec = self._optional_dict(params, "spec")
        async with self._rpc_errors():
            result = await self._service.update_bundle(
                name=name,
                rename=rename,
                description=description,
                description_set="description" in params,
                spec=spec,
            )
        return result

    async def activate(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        version = params.get("version")
        if version is not None and not isinstance(version, str):
            raise TypeError("version must be a string or null")
        async with self._rpc_errors():
            result = await self._service.activate_bundle(name=name, version=version)
        return result

    async def list_(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        conversation_id = params.get("conversation_id")
        if conversation_id is not None and not isinstance(conversation_id, str):
            raise TypeError("conversation_id must be a string")
        limit = int(params.get("limit", 50))
        offset = int(params.get("offset", 0))
        async with self._rpc_errors():
            snapshots = await self._service.list_snapshots(
                conversation_id=conversation_id,
                limit=limit,
                offset=offset,
            )
        return {"snapshots": snapshots}

    async def inspect(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        context_id = self._require_str(params, "context_id")
        async with self._rpc_errors():
            entry = await self._service.inspect_context(context_id=context_id)
        return entry

    async def get(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        snapshot_id = self._require_str(params, "snapshot_id")
        async with self._rpc_errors():
            entry = await self._service.get_snapshot(snapshot_id=snapshot_id)
        return {"snapshot": entry}

    async def traces(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        conversation_id = params.get("conversation_id")
        if conversation_id is not None and not isinstance(conversation_id, str):
            raise TypeError("conversation_id must be a string")
        limit = int(params.get("limit", 50))
        offset = int(params.get("offset", 0))
        async with self._rpc_errors():
            traces = await self._service.list_traces(
                conversation_id=conversation_id,
                limit=limit,
                offset=offset,
            )
        return {"traces": traces}

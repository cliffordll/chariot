"""sidecar prompt RPC adapters."""

from __future__ import annotations

from typing import Any

from chariot.rpc.jsonrpc import RpcContext
from chariot.sidecar.methods import MethodBase
from chariot.sidecar.services import PromptService


class PromptMethods(MethodBase):
    def __init__(self, runtime) -> None:  # type: ignore[no-untyped-def]
        super().__init__(runtime)
        self._service = PromptService(runtime)

    async def list_(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del params, ctx
        async with self._session() as session:
            bundles = await self._service.list_bundles(session)
        return {"bundles": bundles}

    async def show(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        async with self._session() as session:
            bundle = await self._service.get_bundle(session, name=name)
        return {"bundle": bundle}

    async def versions(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        bundle_name = self._require_str(params, "bundle_name")
        async with self._session() as session:
            versions = await self._service.list_versions(session, bundle_name=bundle_name)
        return {"versions": versions}

    async def version(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        bundle_name = self._require_str(params, "bundle_name")
        version = self._require_str(params, "version")
        async with self._session() as session:
            entry = await self._service.get_version(session, bundle_name=bundle_name, version=version)
        return {"version": entry}

    async def traces(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        bundle_name = params.get("bundle_name")
        if bundle_name is not None and not isinstance(bundle_name, str):
            raise TypeError("bundle_name must be a string")
        limit = int(params.get("limit", 50))
        offset = int(params.get("offset", 0))
        async with self._session() as session:
            traces = await self._service.list_traces(
                session,
                bundle_name=bundle_name,
                limit=limit,
                offset=offset,
            )
        return {"traces": traces}

    async def inspect(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        trace_id = self._require_str(params, "trace_id")
        async with self._session() as session:
            trace = await self._service.inspect_trace(session, trace_id=trace_id)
        return {"trace": trace}

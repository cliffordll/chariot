"""sidecar prompt RPC adapters."""

from __future__ import annotations

from typing import Any

from chariot.rpc.jsonrpc import RpcContext
from chariot.sidecar.methods import MethodBase
from chariot.sidecar.services import PromptService

_MISSING = object()


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
            entry = await self._service.get_version(
                session, bundle_name=bundle_name, version=version
            )
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

    async def add(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        description = self._optional_str(params, "description")
        layers = self._optional_list_of_dicts(params, "layers")
        async with self._session() as session:
            result = await self._service.add_bundle(
                session,
                name=name,
                description=description,
                layers=layers,
            )
        return result

    async def update(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        description = params.get("description", _MISSING)
        if (
            description is not _MISSING
            and description is not None
            and not isinstance(description, str)
        ):
            raise TypeError("description must be a string or null")
        layers = self._optional_list_of_dicts(params, "layers")
        async with self._session() as session:
            result = await self._service.update_bundle(
                session,
                name=name,
                description=description,
                description_set=description is not _MISSING,
                layers=layers,
            )
        return result

    async def activate(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        version = params.get("version")
        if version is not None and not isinstance(version, str):
            raise TypeError("version must be a string or null")
        async with self._session() as session:
            result = await self._service.activate_bundle(session, name=name, version=version)
        return result

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

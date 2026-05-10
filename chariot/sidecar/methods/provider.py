"""sidecar provider RPC adapters."""

from __future__ import annotations

from typing import Any

from chariot.rpc.jsonrpc import RpcContext
from chariot.sidecar.methods import MethodBase
from chariot.sidecar.runtime import SidecarRuntime
from chariot.sidecar.services import ProviderService


class ProviderMethods(MethodBase):
    def __init__(self, runtime: SidecarRuntime) -> None:
        super().__init__(runtime)
        self._service = ProviderService(runtime)

    async def list_(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del params, ctx
        async with self._session() as session:
            providers = await self._service.list_entries(session)
        return {"providers": providers}

    async def show(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        async with self._session() as session:
            provider = await self._service.show_entry(session, name=name)
        return {"provider": provider}

    async def add(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        type_ = self._require_str(params, "type")
        options = self._require_dict(params, "options")
        params_field = self._optional_dict(params, "params") or {}
        async with self._session() as session:
            provider = await self._service.add_entry(
                session,
                name=name,
                type_=type_,
                options=options,
                params=params_field,
            )
        return {"provider": provider}

    async def update(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        type_ = self._optional_str(params, "type")
        options = self._optional_dict(params, "options")
        params_field = self._optional_dict(params, "params")
        async with self._session() as session:
            provider = await self._service.update_entry(
                session,
                name=name,
                type_=type_,
                options=options,
                params=params_field,
            )
        return {"provider": provider}

    async def delete(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        async with self._session() as session:
            deleted = await self._service.delete_entry(session, name=name)
        return {"deleted": deleted}

    async def probe(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        async with self._session() as session:
            return await self._service.probe(session, name=name)

    async def status(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del params, ctx
        async with self._session() as session:
            return await self._service.status(session)

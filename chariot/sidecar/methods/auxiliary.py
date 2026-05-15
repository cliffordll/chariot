"""sidecar auxiliary client RPC adapters(B3 wave 2)。

通过 `AuxiliaryApi` 进入 service 层，由 service 统一处理 repo 访问与异常。
"""

from __future__ import annotations

from typing import Any

from chariot.models.agent import ClearableStr
from chariot.models.auxiliary import AuxiliaryClientEntry
from chariot.rpc.jsonrpc import JsonRpcServer, RpcContext, RpcError
from chariot.sidecar.methods import MethodBase
from chariot.sidecar.services import AuxiliaryApi


class AuxiliaryMethods(MethodBase):
    async def list_(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del params, ctx
        async with self._rpc_errors():
            entries = await AuxiliaryApi(self.runtime).list_entries()
        return {"auxiliary_clients": [self._serialize(e) for e in entries]}

    async def show(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        async with self._rpc_errors():
            entry = await AuxiliaryApi(self.runtime).get_entry(name)
        if entry is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"auxiliary client {name!r} not found")
        return {"auxiliary_client": self._serialize(entry)}

    async def add(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        provider_id = self._provider_ref(params)
        model: ClearableStr = self._optional_str(params, "model")
        params_field = self._optional_dict(params, "params") or {}
        async with self._rpc_errors():
            entry = await AuxiliaryApi(self.runtime).create(
                name=name,
                provider_id=provider_id,
                model=model,
                params=params_field,
            )
        return {"auxiliary_client": self._serialize(entry)}

    async def update(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        provider_id = self._optional_provider_ref(params)
        model: ClearableStr = self._clearable_str(params, "model")
        params_field = self._optional_dict(params, "params")
        async with self._rpc_errors():
            entry = await AuxiliaryApi(self.runtime).update(
                name,
                provider_id=provider_id,
                model=model,
                params=params_field,
            )
        return {"auxiliary_client": self._serialize(entry)}

    async def delete(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        async with self._rpc_errors():
            await AuxiliaryApi(self.runtime).delete(name)
        return {"deleted": name}

    @staticmethod
    def _serialize(entry: AuxiliaryClientEntry) -> dict[str, Any]:
        return {
            "name": entry.name,
            "provider_id": entry.provider_id,
            "model": entry.model,
            "params": entry.params,
        }

    def _provider_ref(self, params: dict[str, Any]) -> str:
        return self._require_str(params, "provider_id")

    def _optional_provider_ref(self, params: dict[str, Any]) -> str | None:
        return self._optional_str(params, "provider_id")

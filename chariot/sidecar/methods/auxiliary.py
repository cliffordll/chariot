"""sidecar auxiliary client RPC adapters(B3 wave 2)。

直接走 `AuxiliaryRepo`,无中间 service 层 —— surface 复杂度低,后续若引入
provider_entry 校验 / probe 逻辑再抽 service。

异常翻译走 `MethodBase._session()` 的统一映射(AuxiliaryClientNotFound →
ERR_NOT_FOUND;DuplicateAuxiliaryClientName → ERR_DUPLICATE;ConfigError
→ ERR_INVALID_PARAMS)。
"""

from __future__ import annotations

from typing import Any

from chariot.models.auxiliary import AuxiliaryClientEntry
from chariot.repos.auxiliary_repo import AuxiliaryRepo
from chariot.rpc.jsonrpc import JsonRpcServer, RpcContext, RpcError
from chariot.sidecar.methods import MethodBase


class AuxiliaryMethods(MethodBase):
    async def list_(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del params, ctx
        async with self._session() as session:
            entries = await AuxiliaryRepo(session).list_entries()
        return {"auxiliary_clients": [self._serialize(e) for e in entries]}

    async def show(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        async with self._session() as session:
            entry = await AuxiliaryRepo(session).get_entry(name)
        if entry is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"auxiliary client {name!r} not found")
        return {"auxiliary_client": self._serialize(entry)}

    async def add(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        provider_entry = self._require_str(params, "provider_entry")
        model = self._optional_str(params, "model")
        params_field = self._optional_dict(params, "params") or {}
        async with self._session() as session:
            entry = await AuxiliaryRepo(session).create(
                name=name,
                provider_entry=provider_entry,
                model=model,
                params=params_field,
            )
        return {"auxiliary_client": self._serialize(entry)}

    async def update(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        provider_entry = self._optional_str(params, "provider_entry")
        model = self._clearable_str(params, "model")
        params_field = self._optional_dict(params, "params")
        async with self._session() as session:
            entry = await AuxiliaryRepo(session).update(
                name,
                provider_entry=provider_entry,
                model=model,
                params=params_field,
            )
        return {"auxiliary_client": self._serialize(entry)}

    async def delete(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        async with self._session() as session:
            await AuxiliaryRepo(session).delete(name)
        return {"deleted": name}

    @staticmethod
    def _serialize(entry: AuxiliaryClientEntry) -> dict[str, Any]:
        return {
            "name": entry.name,
            "provider_entry": entry.provider_entry,
            "model": entry.model,
            "params": entry.params,
        }

"""sidecar checkpoint RPC adapters(B5 wave 3)。

写路径(create / rollback / delete)走 `agent.checkpoint_manager`(三件套 + audit);
读路径(list / show)走 `CheckpointApi`。
"""

from __future__ import annotations

from typing import Any

from chariot.rpc.jsonrpc import JsonRpcServer, RpcContext, RpcError
from chariot.sidecar.methods import MethodBase
from chariot.sidecar.services import CheckpointApi


class CheckpointMethods(MethodBase):
    async def list_(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx, params
        entries = await CheckpointApi(self.runtime).list_entries()
        return {"checkpoints": [self._entry_to_dict(e) for e in entries]}

    async def show(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        checkpoint_id = self._require_str(params, "checkpoint_id")
        entry = await CheckpointApi(self.runtime).get_entry(checkpoint_id)
        if entry is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"checkpoint {checkpoint_id!r} not found")
        return {"checkpoint": self._entry_to_dict(entry)}

    async def create(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        manager = self.agent.checkpoint_manager
        if manager is None:
            raise RpcError(JsonRpcServer.ERR_INVALID_PARAMS, "checkpoint manager not available")
        entry = await manager.create(name)
        return {"checkpoint": self._entry_to_dict(entry)}

    async def rollback(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        checkpoint_id = self._require_str(params, "checkpoint_id")
        manager = self.agent.checkpoint_manager
        if manager is None:
            raise RpcError(JsonRpcServer.ERR_INVALID_PARAMS, "checkpoint manager not available")
        result = await manager.rollback(checkpoint_id)
        return {
            "git_ok": result.git_ok,
            "db_ok": result.db_ok,
            "config_ok": result.config_ok,
            "restored": result.restored,
            "errors": result.errors,
        }

    async def delete(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        checkpoint_id = self._require_str(params, "checkpoint_id")
        manager = self.agent.checkpoint_manager
        if manager is None:
            raise RpcError(JsonRpcServer.ERR_INVALID_PARAMS, "checkpoint manager not available")
        ok = await manager.delete(checkpoint_id)
        if not ok:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"checkpoint {checkpoint_id!r} not found")
        return {"deleted": checkpoint_id}

    @staticmethod
    def _entry_to_dict(entry: Any) -> dict[str, Any]:
        return {
            "id": entry.id,
            "name": entry.name,
            "kind": entry.kind,
            "target": entry.target,
            "payload": entry.payload,
            "created_at": entry.created_at.isoformat(),
        }

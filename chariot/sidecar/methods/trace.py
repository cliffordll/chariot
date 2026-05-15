"""sidecar trace RPC adapters."""

from __future__ import annotations

from typing import Any

from chariot.rpc.jsonrpc import JsonRpcServer, RpcContext, RpcError
from chariot.sidecar.methods import MethodBase
from chariot.sidecar.runtime import SidecarRuntime
from chariot.sidecar.services import TraceApi


class TraceMethods(MethodBase):
    def __init__(self, runtime: SidecarRuntime) -> None:
        super().__init__(runtime)
        self._service = TraceApi(runtime)

    async def list_(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        conversation_id = self._optional_str(params, "conversation_id")
        task_id = self._optional_str(params, "task_id")
        provider_name = self._optional_str(params, "provider_name")
        status = self._optional_str(params, "status")
        limit = self._optional_int(params, "limit", default=50)
        offset = self._optional_int(params, "offset", default=0)
        async with self._rpc_errors():
            turns = await self._service.list_turns(
                conversation_id=conversation_id,
                task_id=task_id,
                provider_name=provider_name,
                status=status,
                limit=limit,
                offset=offset,
            )
        return {"turns": turns}

    async def show(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        turn_id = self._require_str(params, "turn_id")
        async with self._rpc_errors():
            turn = await self._service.get_turn(turn_id=turn_id)
        return {"turn": turn}

    async def view(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        turn_id = self._require_str(params, "turn_id")
        async with self._rpc_errors():
            tree = await self._service.get_tree(turn_id=turn_id)
        return tree

    async def reconcile(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        older_than_seconds = self._optional_int(params, "older_than_seconds", default=3600)
        async with self._rpc_errors():
            return await self._service.reconcile_stale(older_than_seconds=older_than_seconds)

    @staticmethod
    def _optional_int(params: dict[str, Any], key: str, *, default: int) -> int:
        val = params.get(key)
        if val is None:
            return default
        if not isinstance(val, int) or isinstance(val, bool):
            raise RpcError(JsonRpcServer.ERR_INVALID_PARAMS, f"{key!r} must be an integer")
        return val

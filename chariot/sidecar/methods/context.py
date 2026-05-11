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

    async def list_(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        conversation_id = params.get("conversation_id")
        if conversation_id is not None and not isinstance(conversation_id, str):
            raise TypeError("conversation_id must be a string")
        limit = int(params.get("limit", 50))
        offset = int(params.get("offset", 0))
        async with self._session() as session:
            snapshots = await self._service.list_snapshots(
                session,
                conversation_id=conversation_id,
                limit=limit,
                offset=offset,
            )
        return {"snapshots": snapshots}

    async def inspect(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        context_id = self._require_str(params, "context_id")
        async with self._session() as session:
            entry = await self._service.inspect_context(session, context_id=context_id)
        return entry

    async def get(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        snapshot_id = self._require_str(params, "snapshot_id")
        async with self._session() as session:
            entry = await self._service.get_snapshot(session, snapshot_id=snapshot_id)
        return {"snapshot": entry}

    async def traces(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        conversation_id = params.get("conversation_id")
        if conversation_id is not None and not isinstance(conversation_id, str):
            raise TypeError("conversation_id must be a string")
        limit = int(params.get("limit", 50))
        offset = int(params.get("offset", 0))
        async with self._session() as session:
            traces = await self._service.list_traces(
                session,
                conversation_id=conversation_id,
                limit=limit,
                offset=offset,
            )
        return {"traces": traces}

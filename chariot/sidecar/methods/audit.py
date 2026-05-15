"""sidecar audit RPC adapters(B5 wave 2)。

只读 RPC,把 `audit_events` 表暴露给 surface 浏览。写路径由
`AuditHookManager` 在 ToolExecutionService / MemoryRepo / CheckpointManager
等命令链上自动触发,不走 RPC。
"""

from __future__ import annotations

from typing import Any

from chariot.rpc.jsonrpc import JsonRpcServer, RpcContext, RpcError
from chariot.sidecar.methods import MethodBase
from chariot.sidecar.services import AuditApi


class AuditMethods(MethodBase):
    async def list_(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        limit = int(params.get("limit", 50))
        events = await AuditApi(self.runtime).list_events(limit=limit)
        return {"events": events}

    async def show(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        event_id = self._require_str(params, "event_id")
        event = await AuditApi(self.runtime).get_event(event_id)
        if event is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"audit event {event_id!r} not found")
        return {"event": event}

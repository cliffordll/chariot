"""sidecar audit RPC adapters(B5 wave 2)。

只读 RPC,把 `audit_events` 表暴露给 surface 浏览。写路径由
`AuditHookManager` 在 ToolExecutionService / MemoryRepo / CheckpointManager
等命令链上自动触发,不走 RPC。
"""

from __future__ import annotations

from typing import Any

from chariot.repos.audit_repo import AuditEvent, AuditRepo
from chariot.rpc.jsonrpc import JsonRpcServer, RpcContext, RpcError
from chariot.sidecar.methods import MethodBase


class AuditMethods(MethodBase):
    async def list_(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        limit = int(params.get("limit", 50))
        async with self._session() as session:
            events = await AuditRepo(session).list_events(limit=limit)
        return {"events": [self._event_to_dict(ev) for ev in events]}

    async def show(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        event_id = self._require_str(params, "event_id")
        async with self._session() as session:
            event = await AuditRepo(session).get_event(event_id)
        if event is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"audit event {event_id!r} not found")
        return {"event": self._event_to_dict(event)}

    @staticmethod
    def _event_to_dict(event: AuditEvent) -> dict[str, Any]:
        return {
            "id": event.id,
            "event_type": event.event_type,
            "status": event.status,
            "payload": event.payload,
            "created_at": event.created_at.isoformat(),
        }

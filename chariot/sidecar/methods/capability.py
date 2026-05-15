"""sidecar capability RPC adapters(B5 wave 3)。

读 / 写 `capabilities` 表;CLI 全局 `--yolo` 不走 RPC(per-process)。
"""

from __future__ import annotations

from typing import Any

from chariot.rpc.jsonrpc import RpcContext
from chariot.sidecar.methods import MethodBase
from chariot.sidecar.services import CapabilityApi


class CapabilityMethods(MethodBase):
    async def list_(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx, params
        entries = await CapabilityApi(self.runtime).list_entries()
        return {"capabilities": [self._entry_to_dict(e) for e in entries]}

    async def set_(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        enabled = bool(params.get("enabled", False))
        entry = await CapabilityApi(self.runtime).set_enabled(name, enabled)
        return {"capability": self._entry_to_dict(entry)}

    @staticmethod
    def _entry_to_dict(entry: Any) -> dict[str, Any]:
        return {
            "name": entry.name,
            "enabled": entry.enabled,
            "updated_at": entry.updated_at.isoformat(),
        }

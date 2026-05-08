"""sidecar tool method handlers(0.6.5 S.8c)。

`ToolMethods`:list / enable / disable / config 4 个 method。

0.4.0 起 tool 表是 seeded fixture(4 条:read_file / list_dir / shell_exec /
http_get),**不开放** add/delete —— 只允许改 `enabled` / `options`。
"""

from __future__ import annotations

from typing import Any

from chariot.agent.config import ToolEntry
from chariot.repos.tool_repo import ToolRepo
from chariot.rpc.jsonrpc import RpcContext
from chariot.sidecar.methods import MethodBase


class ToolMethods(MethodBase):
    """tool CRUD method handlers(list_tools / enable_tool / disable_tool / config_tool)。"""

    async def list_(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """`list_tools`:列所有 tool entry(含 disabled)。"""
        del params, ctx
        async with self._session() as session:
            entries = await ToolRepo(session).list_entries()
        return {"tools": [self._serialize(t) for t in entries]}

    async def enable(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """`enable_tool`:置 enabled=True。"""
        del ctx
        return await self._set_enabled(params, enabled=True)

    async def disable(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """`disable_tool`:置 enabled=False。"""
        del ctx
        return await self._set_enabled(params, enabled=False)

    async def _set_enabled(self, params: dict[str, Any], *, enabled: bool) -> dict[str, Any]:
        name = self._require_str(params, "name")
        async with self._session() as session:
            entry = await ToolRepo(session).update(name, enabled=enabled)
        return {"tool": self._serialize(entry)}

    async def config(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """`config_tool`:改 options dict。options 不合法走 ToolRegistry.build
        校验,失败抛 ConfigError → 翻译 ERR_INVALID_PARAMS。
        """
        del ctx
        name = self._require_str(params, "name")
        options = self._require_dict(params, "options")
        async with self._session() as session:
            entry = await ToolRepo(session).update(name, options=options)
        return {"tool": self._serialize(entry)}

    @staticmethod
    def _serialize(entry: ToolEntry) -> dict[str, Any]:
        """`ToolEntry` dataclass → wire dict。"""
        return {
            "name": entry.name,
            "type": entry.type,
            "enabled": entry.enabled,
            "options": entry.options,
        }

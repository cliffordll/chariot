"""sidecar provider method handlers(0.6.5 S.8c)。

`ProviderMethods`:list / add / edit / delete / probe 5 个 method。
delete 不联动删 logs(logs.provider 是裸字段,允许 dangling 引用)。
probe 走 `ProviderProber.probe(entry)`,永不 raise(上游错走 ProbeResult.error)。
"""

from __future__ import annotations

from typing import Any

from chariot.agent.config import ProviderEntry
from chariot.providers.prober import ProviderProber
from chariot.repos.provider_repo import ProviderRepo
from chariot.rpc.jsonrpc import JsonRpcServer, RpcContext, RpcError
from chariot.sidecar.methods import MethodBase


class ProviderMethods(MethodBase):
    """provider CRUD method handlers(list / add / edit / delete / probe)。"""

    async def list_(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """`list_providers`:按 created_at 升序列。

        默认标记:返字段 `default: bool`(同时至多一条 True)。
        """
        del params, ctx
        async with self._session() as session:
            repo = ProviderRepo(session)
            entries = await repo.list_entries()
            default = await repo.get_default()
        default_name = default.name if default else None
        return {
            "providers": [
                {**self._serialize(e), "default": e.name == default_name} for e in entries
            ]
        }

    async def add(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """`add_provider`:新增 entry。重名 → ERR_DUPLICATE。"""
        del ctx
        name = self._require_str(params, "name")
        type_ = self._require_str(params, "type")
        options = self._require_dict(params, "options")
        params_field = self._optional_dict(params, "params") or {}
        async with self._session() as session:
            entry = await ProviderRepo(session).create(
                name=name,
                type=type_,
                options=options,
                params=params_field,
            )
        return {"provider": self._serialize(entry)}

    async def edit(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """`edit_provider`:改 type / options / params(任一字段缺省 = 不动)。

        不允许改 name(name 是 ProviderRepo 的主 key 抽象;要改 name 走删 + 加)。
        """
        del ctx
        name = self._require_str(params, "name")
        type_ = self._optional_str(params, "type")
        options = self._optional_dict(params, "options")
        params_field = self._optional_dict(params, "params")
        async with self._session() as session:
            entry = await ProviderRepo(session).update(
                name,
                type=type_,
                options=options,
                params=params_field,
            )
        return {"provider": self._serialize(entry)}

    async def delete(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """`delete_provider`:删 entry。被引用 logs 不联动删(logs.provider 是
        裸字段,允许 dangling 引用)。
        """
        del ctx
        name = self._require_str(params, "name")
        async with self._session() as session:
            await ProviderRepo(session).delete(name)
        return {"deleted": name}

    async def probe(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """`probe_provider`:对 entry 跑探针。永不 raise — 上游错走 ProbeResult.error。

        注:DB session 仅用来取 entry,probe 本身脱离 session(临时 build
        Provider 实例 + 发 1 token 请求)。
        """
        del ctx
        name = self._require_str(params, "name")
        async with self._session() as session:
            entry = await ProviderRepo(session).get_entry(name)
            if entry is None:
                raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"provider {name!r} not found")
        result = await ProviderProber.probe(entry)
        return {
            "ok": result.ok,
            "latency_ms": result.latency_ms,
            "error": (
                {"code": result.error.code, "message": result.error.message}
                if result.error is not None
                else None
            ),
        }

    @staticmethod
    def _serialize(entry: ProviderEntry) -> dict[str, Any]:
        """`ProviderEntry` dataclass → wire dict(不含 default 标记;list_ 自己拼)。"""
        return {
            "name": entry.name,
            "type": entry.type,
            "options": entry.options,
            "params": entry.params,
        }

"""sidecar agent profile RPC adapters."""

from __future__ import annotations

from typing import Any

from chariot.rpc.jsonrpc import RpcContext
from chariot.sidecar.methods import MethodBase
from chariot.sidecar.runtime import SidecarRuntime
from chariot.sidecar.services.agent import AgentApi


class AgentMethods(MethodBase):
    def __init__(self, runtime: SidecarRuntime) -> None:
        super().__init__(runtime)
        self._api = AgentApi(runtime)

    async def list_agents(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del params, ctx
        async with self._session() as session:
            agents = await self._api.list_agents(session)
        return {"agents": agents}

    async def get_agent(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        async with self._session() as session:
            agent = await self._api.get_agent(session, name=name)
        return {"agent": agent}

    async def create_agent(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        role = self._require_str(params, "role")
        prompt_bundle = self._optional_str(params, "prompt_bundle")
        tool_profile = self._optional_str(params, "tool_profile")
        provider_profile = self._optional_str(params, "provider_profile")
        budget = self._optional_dict(params, "budget")
        meta = self._optional_dict(params, "meta")
        reflection_enabled = bool(params.get("reflection_enabled", False))
        reflection_max_retries = self._optional_int(params, "reflection_max_retries", default=2) or 2
        async with self._session() as session:
            agent = await self._api.create_agent(
                session,
                name=name,
                role=role,
                prompt_bundle=prompt_bundle,
                tool_profile=tool_profile,
                provider_profile=provider_profile,
                budget=budget,
                meta=meta,
                reflection_enabled=reflection_enabled,
                reflection_max_retries=reflection_max_retries,
            )
        return {"agent": agent}

    async def update_agent(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        role = self._optional_str(params, "role")
        # 三个 binding 字段走 clearable 语义:key 缺席 → UNSET(skip);null → 清空;str → set
        prompt_bundle = self._clearable_str(params, "prompt_bundle")
        tool_profile = self._clearable_str(params, "tool_profile")
        provider_profile = self._clearable_str(params, "provider_profile")
        budget = self._optional_dict(params, "budget")
        meta = self._optional_dict(params, "meta")
        reflection_enabled = bool(params["reflection_enabled"]) if "reflection_enabled" in params else None
        reflection_max_retries = self._optional_int(params, "reflection_max_retries", default=None)
        async with self._session() as session:
            agent = await self._api.update_agent(
                session,
                name=name,
                role=role,
                prompt_bundle=prompt_bundle,
                tool_profile=tool_profile,
                provider_profile=provider_profile,
                budget=budget,
                meta=meta,
                reflection_enabled=reflection_enabled,
                reflection_max_retries=reflection_max_retries,
            )
        return {"agent": agent}

    @staticmethod
    def _optional_int(params: dict[str, Any], key: str, *, default: int | None) -> int | None:
        from chariot.rpc.jsonrpc import JsonRpcServer, RpcError

        if key not in params:
            return default
        val = params[key]
        if val is None:
            return default
        if not isinstance(val, int) or isinstance(val, bool):
            raise RpcError(JsonRpcServer.ERR_INVALID_PARAMS, f"{key!r} must be an integer")
        return val

    async def delete_agent(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        async with self._session() as session:
            result = await self._api.delete_agent(session, name=name)
        return result

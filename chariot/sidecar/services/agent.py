"""Sidecar agent profile API surface."""

from __future__ import annotations

from typing import Any

from chariot.models.agent import UNSET, AgentProfile, ClearableStr
from chariot.rpc.jsonrpc import JsonRpcServer, RpcError
from chariot.services.agent import AgentService
from chariot.sidecar.runtime import SidecarRuntime


class AgentApi:
    def __init__(self, runtime: SidecarRuntime) -> None:
        self._runtime = runtime

    async def list_agents(self) -> list[dict[str, Any]]:
        service = AgentService(self._runtime)
        return [self._agent_to_dict(entry) for entry in await service.list_agents()]

    async def get_agent(self, *, name: str) -> dict[str, Any]:
        service = AgentService(self._runtime)
        entry = await service.get_agent(name)
        if entry is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"agent profile {name!r} not found")
        return self._agent_to_dict(entry)

    async def create_agent(
        self,
        *,
        name: str,
        role: str,
        prompt_id: str | None = None,
        toolset_id: str | None = None,
        provider_id: str | None = None,
        budget: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        reflection_enabled: bool = False,
        reflection_max_retries: int = 2,
    ) -> dict[str, Any]:
        entry = await AgentService(self._runtime).create_agent(
            name=name,
            role=role,
            prompt_id=prompt_id,
            toolset_id=toolset_id,
            provider_id=provider_id,
            budget=budget,
            meta=meta,
            reflection_enabled=reflection_enabled,
            reflection_max_retries=reflection_max_retries,
        )
        return self._agent_to_dict(entry)

    async def update_agent(
        self,
        *,
        name: str,
        rename: str | None = None,
        role: str | None = None,
        prompt_id: ClearableStr = UNSET,
        toolset_id: ClearableStr = UNSET,
        provider_id: ClearableStr = UNSET,
        budget: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        reflection_enabled: bool | None = None,
        reflection_max_retries: int | None = None,
    ) -> dict[str, Any]:
        try:
            service = AgentService(self._runtime)
            if rename is not None:
                entry = await service.rename_agent(name, rename)
                name = entry.id or entry.name
            entry = await service.update_agent(
                name=name,
                role=role,
                prompt_id=prompt_id,
                toolset_id=toolset_id,
                provider_id=provider_id,
                budget=budget,
                meta=meta,
                reflection_enabled=reflection_enabled,
                reflection_max_retries=reflection_max_retries,
            )
        except ValueError as e:
            self._raise_rpc_error(e)
        return self._agent_to_dict(entry)

    async def delete_agent(self, *, name: str) -> dict[str, Any]:
        try:
            await AgentService(self._runtime).delete_agent(name)
        except ValueError as e:
            self._raise_rpc_error(e)
        return {"deleted": name}

    @staticmethod
    def _raise_rpc_error(exc: ValueError) -> None:
        message = str(exc)
        if "not found" in message:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, message) from exc
        raise RpcError(JsonRpcServer.ERR_INVALID_PARAMS, message) from exc

    @staticmethod
    def _agent_to_dict(entry: AgentProfile) -> dict[str, Any]:
        return {
            "id": entry.id,
            "name": entry.name,
            "role": entry.role,
            "prompt_id": entry.prompt_id,
            "toolset_id": entry.toolset_id,
            "provider_id": entry.provider_id,
            "budget": entry.budget,
            "meta": entry.meta,
            "reflection_enabled": entry.reflection_enabled,
            "reflection_max_retries": entry.reflection_max_retries,
            "created_at": entry.created_at.isoformat(),
            "updated_at": entry.updated_at.isoformat(),
        }

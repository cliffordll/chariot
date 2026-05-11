"""Sidecar agent profile API surface."""

from __future__ import annotations

from typing import Any

from chariot.models.agent import UNSET, AgentProfile, ClearableStr
from chariot.repos.task_repo import TaskRepo
from chariot.rpc.jsonrpc import JsonRpcServer, RpcError
from chariot.services.agent import AgentService
from chariot.sidecar.runtime import SidecarRuntime


class AgentApi:
    def __init__(self, runtime: SidecarRuntime) -> None:
        self._runtime = runtime

    async def list_agents(self, session: Any) -> list[dict[str, Any]]:
        service = AgentService(TaskRepo(session))
        return [self._agent_to_dict(entry) for entry in await service.list_agents()]

    async def get_agent(self, session: Any, *, name: str) -> dict[str, Any]:
        service = AgentService(TaskRepo(session))
        entry = await service.get_agent(name)
        if entry is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"agent profile {name!r} not found")
        return self._agent_to_dict(entry)

    async def create_agent(
        self,
        session: Any,
        *,
        name: str,
        role: str,
        prompt_bundle: str | None = None,
        tool_profile: str | None = None,
        provider_profile: str | None = None,
        budget: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry = await AgentService(TaskRepo(session)).create_agent(
            name=name,
            role=role,
            prompt_bundle=prompt_bundle,
            tool_profile=tool_profile,
            provider_profile=provider_profile,
            budget=budget,
            meta=meta,
        )
        return self._agent_to_dict(entry)

    async def update_agent(
        self,
        session: Any,
        *,
        name: str,
        role: str | None = None,
        prompt_bundle: ClearableStr = UNSET,
        tool_profile: ClearableStr = UNSET,
        provider_profile: ClearableStr = UNSET,
        budget: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            entry = await AgentService(TaskRepo(session)).update_agent(
                name=name,
                role=role,
                prompt_bundle=prompt_bundle,
                tool_profile=tool_profile,
                provider_profile=provider_profile,
                budget=budget,
                meta=meta,
            )
        except ValueError as e:
            self._raise_rpc_error(e)
        return self._agent_to_dict(entry)

    async def delete_agent(self, session: Any, *, name: str) -> dict[str, Any]:
        try:
            await AgentService(TaskRepo(session)).delete_agent(name)
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
            "name": entry.name,
            "role": entry.role,
            "prompt_bundle": entry.prompt_bundle,
            "tool_profile": entry.tool_profile,
            "provider_profile": entry.provider_profile,
            "budget": entry.budget,
            "meta": entry.meta,
            "created_at": entry.created_at.isoformat(),
            "updated_at": entry.updated_at.isoformat(),
        }

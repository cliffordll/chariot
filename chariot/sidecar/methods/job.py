"""sidecar scheduled job RPC adapters."""

from __future__ import annotations

from typing import Any

from chariot.rpc.jsonrpc import RpcContext
from chariot.sidecar.methods import MethodBase
from chariot.sidecar.runtime import SidecarRuntime
from chariot.sidecar.services.job import JobApi


class JobMethods(MethodBase):
    def __init__(self, runtime: SidecarRuntime) -> None:
        super().__init__(runtime)
        self._api = JobApi(runtime)

    async def list_jobs(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del params, ctx
        async with self._session() as session:
            jobs = await self._api.list_jobs(session)
        return {"jobs": jobs}

    async def show_job(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        async with self._session() as session:
            job = await self._api.get_job(session, name=name)
        return {"job": job}

    async def create_job(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        goal = self._require_str(params, "goal")
        cron = self._require_str(params, "cron")
        agent_profile = self._optional_str(params, "agent_profile")
        meta = self._optional_dict(params, "meta")
        enabled = params.get("enabled", True)
        if not isinstance(enabled, bool):
            raise TypeError("'enabled' must be a boolean")
        async with self._session() as session:
            job = await self._api.create_job(
                session,
                name=name,
                goal=goal,
                cron=cron,
                enabled=enabled,
                agent_profile=agent_profile,
                meta=meta,
            )
        return {"job": job}

    async def update_job(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        goal = self._optional_str(params, "goal")
        cron = self._optional_str(params, "cron")
        agent_profile = self._optional_str(params, "agent_profile")
        meta = self._optional_dict(params, "meta")
        async with self._session() as session:
            job = await self._api.update_job(
                session,
                name=name,
                goal=goal,
                cron=cron,
                agent_profile=agent_profile,
                meta=meta,
            )
        return {"job": job}

    async def enable_job(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        async with self._session() as session:
            job = await self._api.enable_job(session, name=name)
        return {"job": job}

    async def disable_job(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        async with self._session() as session:
            job = await self._api.disable_job(session, name=name)
        return {"job": job}

    async def delete_job(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        async with self._session() as session:
            result = await self._api.delete_job(session, name=name)
        return result

    async def run_job_now(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        async with self._session() as session:
            result = await self._api.run_job_now(session, name=name)
        return result

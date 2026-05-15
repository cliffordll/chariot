"""Sidecar scheduled job API surface."""

from __future__ import annotations

from typing import Any

from chariot.models.job import JobRunRecord, ScheduledJob
from chariot.rpc.jsonrpc import JsonRpcServer, RpcError
from chariot.services.job import JobService
from chariot.sidecar.runtime import SidecarRuntime
from chariot.sidecar.services.task import TaskApi


class JobApi:
    def __init__(self, runtime: SidecarRuntime) -> None:
        self._runtime = runtime

    async def list_jobs(self) -> list[dict[str, Any]]:
        service = JobService(self._runtime)
        return [self._job_to_dict(entry) for entry in await service.list_jobs()]

    async def get_job(self, *, name: str) -> dict[str, Any]:
        service = JobService(self._runtime)
        job = await service.get_job(name)
        if job is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"job {name!r} not found")
        payload = self._job_to_dict(job)
        payload["runs"] = [self._job_run_to_dict(run) for run in await service.list_job_runs(name)]
        return payload

    async def create_job(
        self,
        *,
        name: str,
        goal: str,
        cron: str,
        enabled: bool = True,
        agent_profile: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        service = JobService(self._runtime)
        try:
            job = await service.create_job(
                name=name,
                goal=goal,
                cron=cron,
                enabled=enabled,
                agent_profile=agent_profile,
                meta=meta,
            )
        except ValueError as e:
            self._raise_rpc_error(e)
        return self._job_to_dict(job)

    async def update_job(
        self,
        *,
        name: str,
        goal: str | None = None,
        cron: str | None = None,
        agent_profile: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        service = JobService(self._runtime)
        try:
            job = await service.update_job(
                name=name,
                goal=goal,
                cron=cron,
                agent_profile=agent_profile,
                meta=meta,
            )
        except ValueError as e:
            self._raise_rpc_error(e)
        return self._job_to_dict(job)

    async def enable_job(self, *, name: str) -> dict[str, Any]:
        return await self._set_enabled(name=name, enabled=True)

    async def disable_job(self, *, name: str) -> dict[str, Any]:
        return await self._set_enabled(name=name, enabled=False)

    async def delete_job(self, *, name: str) -> dict[str, Any]:
        service = JobService(self._runtime)
        try:
            await service.delete_job(name)
        except ValueError as e:
            self._raise_rpc_error(e)
        return {"deleted": name}

    async def run_job_now(self, *, name: str) -> dict[str, Any]:
        service = JobService(self._runtime)
        try:
            task, run = await service.run_job_now(name)
        except ValueError as e:
            self._raise_rpc_error(e)
        return {"task": TaskApi._task_to_dict(task), "job_run": self._job_run_to_dict(run)}

    async def _set_enabled(self, *, name: str, enabled: bool) -> dict[str, Any]:
        service = JobService(self._runtime)
        try:
            job = await (service.enable_job(name) if enabled else service.disable_job(name))
        except ValueError as e:
            self._raise_rpc_error(e)
        return self._job_to_dict(job)

    @staticmethod
    def _raise_rpc_error(exc: ValueError) -> None:
        message = str(exc)
        if "not found" in message:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, message) from exc
        raise RpcError(JsonRpcServer.ERR_INVALID_PARAMS, message) from exc

    @staticmethod
    def _job_to_dict(entry: ScheduledJob) -> dict[str, Any]:
        return {
            "name": entry.name,
            "goal": entry.goal,
            "cron": entry.cron,
            "enabled": entry.enabled,
            "agent_profile": entry.agent_profile,
            "last_run_status": entry.last_run_status,
            "last_run_at": entry.last_run_at.isoformat() if entry.last_run_at is not None else None,
            "next_run_at": entry.next_run_at.isoformat() if entry.next_run_at is not None else None,
            "meta": entry.meta,
            "created_at": entry.created_at.isoformat(),
            "updated_at": entry.updated_at.isoformat(),
        }

    @staticmethod
    def _job_run_to_dict(entry: JobRunRecord) -> dict[str, Any]:
        return {
            "id": entry.id,
            "job_name": entry.job_name,
            "task_id": entry.task_id,
            "status": entry.status,
            "error": entry.error,
            "started_at": entry.started_at.isoformat(),
            "finished_at": entry.finished_at.isoformat() if entry.finished_at is not None else None,
        }

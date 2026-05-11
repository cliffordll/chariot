"""Task service for sidecar task/agent/job methods."""

from __future__ import annotations

from typing import Any

from chariot.delegation.models import DelegatedTaskSpec, DelegationRequest
from chariot.delegation.service import DelegationService
from chariot.repos.task_repo import TaskRepo
from chariot.rpc.jsonrpc import JsonRpcServer, RpcError
from chariot.sidecar.runtime import SidecarRuntime
from chariot.tasks.models import TaskCreate, TaskKind, TaskRunCreate
from chariot.tasks.service import AgentService, JobService, TaskService


class TaskManagementService:
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
        prompt_bundle: str | None = None,
        tool_profile: str | None = None,
        provider_profile: str | None = None,
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
            self._raise_task_error(e)
        return self._agent_to_dict(entry)

    async def delete_agent(self, session: Any, *, name: str) -> dict[str, Any]:
        try:
            await AgentService(TaskRepo(session)).delete_agent(name)
        except ValueError as e:
            self._raise_task_error(e)
        return {"deleted": name}

    async def list_tasks(self, session: Any, *, parent_task_id: str | None = None) -> list[dict[str, Any]]:
        service = TaskService(TaskRepo(session))
        return [self._task_to_dict(entry) for entry in await service.list_tasks(parent_task_id=parent_task_id)]

    async def get_task(self, session: Any, *, task_id: str) -> dict[str, Any]:
        service = TaskService(TaskRepo(session))
        task = await service.get_task(task_id)
        if task is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"task {task_id!r} not found")
        child_summary = await service.summarize_child_statuses(task.id)
        runs = await TaskRepo(session).list_runs(task.id)
        payload = self._task_to_dict(task)
        payload["child_status_summary"] = child_summary
        payload["runs"] = [self._run_to_dict(run) for run in runs]
        return payload

    async def get_task_run(self, session: Any, *, run_id: str) -> dict[str, Any]:
        run = await TaskService(TaskRepo(session)).get_task_run(run_id)
        if run is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"task run {run_id!r} not found")
        return self._run_to_dict(run)

    async def list_task_runs(self, session: Any, *, task_id: str) -> list[dict[str, Any]]:
        service = TaskService(TaskRepo(session))
        try:
            runs = await service.list_task_runs(task_id)
        except ValueError as e:
            self._raise_task_error(e)
        return [self._run_to_dict(run) for run in runs]

    async def list_jobs(self, session: Any) -> list[dict[str, Any]]:
        repo = TaskRepo(session)
        service = JobService(repo, TaskService(repo))
        return [self._job_to_dict(entry) for entry in await service.list_jobs()]

    async def get_job(self, session: Any, *, name: str) -> dict[str, Any]:
        repo = TaskRepo(session)
        service = JobService(repo, TaskService(repo))
        job = await service.get_job(name)
        if job is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"job {name!r} not found")
        payload = self._job_to_dict(job)
        payload["runs"] = [self._job_run_to_dict(run) for run in await service.list_job_runs(name)]
        return payload

    async def create_job(
        self,
        session: Any,
        *,
        name: str,
        goal: str,
        cron: str,
        enabled: bool = True,
        agent_profile: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        repo = TaskRepo(session)
        service = JobService(repo, TaskService(repo))
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
            self._raise_task_error(e)
        return self._job_to_dict(job)

    async def update_job(
        self,
        session: Any,
        *,
        name: str,
        goal: str | None = None,
        cron: str | None = None,
        agent_profile: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        repo = TaskRepo(session)
        service = JobService(repo, TaskService(repo))
        try:
            job = await service.update_job(
                name=name,
                goal=goal,
                cron=cron,
                agent_profile=agent_profile,
                meta=meta,
            )
        except ValueError as e:
            self._raise_task_error(e)
        return self._job_to_dict(job)

    async def enable_job(self, session: Any, *, name: str) -> dict[str, Any]:
        return await self._set_job_enabled(session, name=name, enabled=True)

    async def disable_job(self, session: Any, *, name: str) -> dict[str, Any]:
        return await self._set_job_enabled(session, name=name, enabled=False)

    async def delete_job(self, session: Any, *, name: str) -> dict[str, Any]:
        repo = TaskRepo(session)
        service = JobService(repo, TaskService(repo))
        try:
            await service.delete_job(name)
        except ValueError as e:
            self._raise_task_error(e)
        return {"deleted": name}

    async def run_job_now(self, session: Any, *, name: str) -> dict[str, Any]:
        repo = TaskRepo(session)
        service = JobService(repo, TaskService(repo))
        try:
            task, run = await service.run_job_now(name)
        except ValueError as e:
            self._raise_task_error(e)
        return {"task": self._task_to_dict(task), "job_run": self._job_run_to_dict(run)}

    async def create_task(
        self,
        session: Any,
        *,
        goal: str,
        kind: str = "interactive",
        agent_profile: str | None = None,
        owner: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        service = TaskService(TaskRepo(session))
        try:
            task = await service.create_task(
                TaskCreate(
                    goal=goal,
                    kind=TaskKind(kind),
                    agent_profile=agent_profile,
                    owner=owner,
                    meta=meta or {},
                )
            )
        except ValueError as e:
            raise RpcError(JsonRpcServer.ERR_INVALID_PARAMS, str(e)) from e
        return self._task_to_dict(task)

    async def pause_task(self, session: Any, *, task_id: str) -> dict[str, Any]:
        return self._task_to_dict(await self._perform_task_transition(session, "pause", task_id))

    async def resume_task(self, session: Any, *, task_id: str) -> dict[str, Any]:
        return self._task_to_dict(await self._perform_task_transition(session, "resume", task_id))

    async def cancel_task(self, session: Any, *, task_id: str) -> dict[str, Any]:
        return self._task_to_dict(await self._perform_task_transition(session, "cancel", task_id))

    async def start_task_run(
        self,
        session: Any,
        *,
        task_id: str,
        trigger: str = "manual",
        resume_from_run_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        service = TaskService(TaskRepo(session))
        try:
            run = await service.start_task_run(
                TaskRunCreate(
                    task_id=task_id,
                    trigger=trigger,
                    resume_from_run_id=resume_from_run_id,
                    meta=meta or {},
                )
            )
        except ValueError as e:
            self._raise_task_error(e)
        return self._run_to_dict(run)

    async def complete_task_run(
        self,
        session: Any,
        *,
        run_id: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        service = TaskService(TaskRepo(session))
        try:
            run = await service.complete_task_run(run_id, result=result, error=error)
        except ValueError as e:
            self._raise_task_error(e)
        return self._run_to_dict(run)

    async def fail_task_run(
        self,
        session: Any,
        *,
        run_id: str,
        error: str,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        service = TaskService(TaskRepo(session))
        try:
            run = await service.fail_task_run(run_id, error=error, result=result)
        except ValueError as e:
            self._raise_task_error(e)
        return self._run_to_dict(run)

    async def cancel_task_run(
        self,
        session: Any,
        *,
        run_id: str,
        error: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        service = TaskService(TaskRepo(session))
        try:
            run = await service.cancel_task_run(run_id, error=error, result=result)
        except ValueError as e:
            self._raise_task_error(e)
        return self._run_to_dict(run)

    async def delegate_task(
        self,
        session: Any,
        *,
        parent_task_id: str,
        tasks: list[dict[str, Any]],
        reason: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        repo = TaskRepo(session)
        task_service = TaskService(repo)
        delegation = DelegationService(task_service)
        specs = [
            DelegatedTaskSpec(
                goal=str(item["goal"]),
                agent_profile=item.get("agent_profile"),
                owner=item.get("owner"),
                meta=dict(item.get("meta") or {}),
            )
            for item in tasks
        ]
        try:
            result = await delegation.delegate(
                DelegationRequest(
                    parent_task_id=parent_task_id,
                    tasks=tuple(specs),
                    reason=reason,
                    meta=meta or {},
                )
            )
        except ValueError as e:
            self._raise_task_error(e)
        return {
            "parent_task_id": result.parent_task_id,
            "child_task_ids": list(result.child_task_ids),
            "requested": result.requested,
            "created": result.created,
        }

    async def _perform_task_transition(self, session: Any, action: str, task_id: str):  # type: ignore[no-untyped-def]
        service = TaskService(TaskRepo(session))
        try:
            if action == "pause":
                return await service.pause_task(task_id)
            if action == "resume":
                return await service.resume_task(task_id)
            if action == "cancel":
                return await service.cancel_task(task_id)
        except ValueError as e:
            self._raise_task_error(e)
        raise RpcError(JsonRpcServer.ERR_INVALID_PARAMS, f"unsupported task action {action!r}")

    async def _set_job_enabled(self, session: Any, *, name: str, enabled: bool) -> dict[str, Any]:
        repo = TaskRepo(session)
        service = JobService(repo, TaskService(repo))
        try:
            job = await (service.enable_job(name) if enabled else service.disable_job(name))
        except ValueError as e:
            self._raise_task_error(e)
        return self._job_to_dict(job)

    @staticmethod
    def _raise_task_error(exc: ValueError) -> None:
        message = str(exc)
        if "not found" in message:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, message) from exc
        raise RpcError(JsonRpcServer.ERR_INVALID_PARAMS, message) from exc

    @staticmethod
    def _agent_to_dict(entry) -> dict[str, Any]:  # type: ignore[no-untyped-def]
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

    @staticmethod
    def _task_to_dict(entry) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        return {
            "id": entry.id,
            "goal": entry.goal,
            "kind": entry.kind.value,
            "status": entry.status.value,
            "agent_profile": entry.agent_profile,
            "parent_task_id": entry.parent_task_id,
            "owner": entry.owner,
            "meta": entry.meta,
            "artifacts": entry.artifacts,
            "created_at": entry.created_at.isoformat(),
            "updated_at": entry.updated_at.isoformat(),
        }

    @staticmethod
    def _run_to_dict(entry) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        return {
            "id": entry.id,
            "task_id": entry.task_id,
            "status": entry.status.value,
            "trigger": entry.trigger,
            "resume_from_run_id": entry.resume_from_run_id,
            "result": entry.result,
            "error": entry.error,
            "meta": entry.meta,
            "started_at": entry.started_at.isoformat(),
            "finished_at": entry.finished_at.isoformat() if entry.finished_at is not None else None,
        }

    @staticmethod
    def _job_to_dict(entry) -> dict[str, Any]:  # type: ignore[no-untyped-def]
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
    def _job_run_to_dict(entry) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        return {
            "id": entry.id,
            "job_name": entry.job_name,
            "task_id": entry.task_id,
            "status": entry.status,
            "error": entry.error,
            "started_at": entry.started_at.isoformat(),
            "finished_at": entry.finished_at.isoformat() if entry.finished_at is not None else None,
        }

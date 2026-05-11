"""Sidecar task API surface."""

from __future__ import annotations

from typing import Any

from chariot.delegation.models import DelegatedTaskSpec, DelegationRequest
from chariot.delegation.service import DelegationService
from chariot.models.task import TaskCreate, TaskKind, TaskRunCreate
from chariot.repos.task_repo import TaskRepo
from chariot.rpc.jsonrpc import JsonRpcServer, RpcError
from chariot.services.task import TaskService
from chariot.sidecar.runtime import SidecarRuntime


class TaskApi:
    def __init__(self, runtime: SidecarRuntime) -> None:
        self._runtime = runtime

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
            self._raise_rpc_error(e)
        return [self._run_to_dict(run) for run in runs]

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
        return self._task_to_dict(await self._perform_transition(session, "pause", task_id))

    async def resume_task(self, session: Any, *, task_id: str) -> dict[str, Any]:
        return self._task_to_dict(await self._perform_transition(session, "resume", task_id))

    async def cancel_task(self, session: Any, *, task_id: str) -> dict[str, Any]:
        return self._task_to_dict(await self._perform_transition(session, "cancel", task_id))

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
            self._raise_rpc_error(e)
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
            self._raise_rpc_error(e)
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
            self._raise_rpc_error(e)
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
            self._raise_rpc_error(e)
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
            self._raise_rpc_error(e)
        return {
            "parent_task_id": result.parent_task_id,
            "child_task_ids": list(result.child_task_ids),
            "requested": result.requested,
            "created": result.created,
        }

    async def _perform_transition(self, session: Any, action: str, task_id: str):  # type: ignore[no-untyped-def]
        service = TaskService(TaskRepo(session))
        try:
            if action == "pause":
                return await service.pause_task(task_id)
            if action == "resume":
                return await service.resume_task(task_id)
            if action == "cancel":
                return await service.cancel_task(task_id)
        except ValueError as e:
            self._raise_rpc_error(e)
        raise RpcError(JsonRpcServer.ERR_INVALID_PARAMS, f"unsupported task action {action!r}")

    @staticmethod
    def _raise_rpc_error(exc: ValueError) -> None:
        message = str(exc)
        if "not found" in message:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, message) from exc
        raise RpcError(JsonRpcServer.ERR_INVALID_PARAMS, message) from exc

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

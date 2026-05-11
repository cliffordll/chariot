"""Task domain service.

Workflow rules for tasks and task runs. The service stays stable even if the
underlying storage moves between repos.
"""

from __future__ import annotations

from typing import Protocol

from chariot.models.task import (
    DelegationRequest,
    DelegationResult,
    Task,
    TaskCreate,
    TaskKind,
    TaskRun,
    TaskRunCreate,
    TaskRunStatus,
    TaskStatus,
)


class TaskStore(Protocol):
    async def create_task(self, spec: TaskCreate) -> Task: ...

    async def update_task(self, task: Task) -> Task: ...

    async def get_task(self, task_id: str) -> Task | None: ...

    async def list_tasks(self, *, parent_task_id: str | None = None) -> list[Task]: ...

    async def create_run(self, spec: TaskRunCreate) -> TaskRun: ...

    async def update_run(self, run: TaskRun) -> TaskRun: ...

    async def get_run(self, run_id: str) -> TaskRun | None: ...

    async def list_runs(self, task_id: str) -> list[TaskRun]: ...


class TaskService:
    def __init__(self, store: TaskStore) -> None:
        self._store = store

    async def create_task(self, spec: TaskCreate) -> Task:
        return await self._store.create_task(spec)

    async def create_child_task(
        self,
        *,
        parent_task_id: str,
        goal: str,
        agent_profile: str | None = None,
        owner: str | None = None,
        meta: dict[str, object] | None = None,
    ) -> Task:
        parent = await self.require_task(parent_task_id)
        if parent.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            raise ValueError(f"cannot delegate from closed parent task {parent_task_id!r}")
        return await self._store.create_task(
            TaskCreate(
                goal=goal,
                kind=TaskKind.DELEGATED,
                agent_profile=agent_profile or parent.agent_profile,
                parent_task_id=parent.id,
                owner=owner or parent.owner,
                meta=dict(meta or {}),
            )
        )

    async def delegate(self, request: DelegationRequest) -> DelegationResult:
        """Create a batch of child tasks under one parent and preserve lineage."""
        child_ids: list[str] = []
        for spec in request.tasks:
            child = await self.create_child_task(
                parent_task_id=request.parent_task_id,
                goal=spec.goal,
                agent_profile=spec.agent_profile,
                owner=spec.owner,
                meta={
                    **request.meta,
                    **spec.meta,
                    "delegation_reason": request.reason,
                },
            )
            child_ids.append(child.id)
        return DelegationResult(
            parent_task_id=request.parent_task_id,
            child_task_ids=tuple(child_ids),
            requested=len(request.tasks),
            created=len(child_ids),
        )

    async def start_task_run(self, spec: TaskRunCreate) -> TaskRun:
        task = await self.require_task(spec.task_id)
        if task.status == TaskStatus.PAUSED or task.status == TaskStatus.QUEUED:
            await self._store.update_task(task.with_status(TaskStatus.RUNNING))
        elif task.status != TaskStatus.RUNNING:
            raise ValueError(f"cannot start run for task in status {task.status}")
        return await self._store.create_run(spec)

    async def complete_task_run(
        self,
        run_id: str,
        *,
        result: dict[str, object] | None = None,
        error: str | None = None,
    ) -> TaskRun:
        return await self._finish_task_run(
            run_id,
            run_status=TaskRunStatus.FAILED if error else TaskRunStatus.COMPLETED,
            task_status=TaskStatus.FAILED if error else TaskStatus.COMPLETED,
            result=result,
            error=error,
        )

    async def fail_task_run(
        self,
        run_id: str,
        *,
        error: str,
        result: dict[str, object] | None = None,
    ) -> TaskRun:
        if not error:
            raise ValueError("error is required when failing a task run")
        return await self._finish_task_run(
            run_id,
            run_status=TaskRunStatus.FAILED,
            task_status=TaskStatus.FAILED,
            result=result,
            error=error,
        )

    async def cancel_task_run(
        self,
        run_id: str,
        *,
        error: str | None = None,
        result: dict[str, object] | None = None,
    ) -> TaskRun:
        return await self._finish_task_run(
            run_id,
            run_status=TaskRunStatus.CANCELLED,
            task_status=TaskStatus.CANCELLED,
            result=result,
            error=error,
        )

    async def _finish_task_run(
        self,
        run_id: str,
        *,
        run_status: TaskRunStatus,
        task_status: TaskStatus,
        result: dict[str, object] | None = None,
        error: str | None = None,
    ) -> TaskRun:
        run = await self.require_run(run_id)
        run = await self._store.update_run(run.finish(status=run_status, result=result, error=error))
        task = await self.require_task(run.task_id)
        await self._store.update_task(task.with_status(task_status))
        return run

    async def cancel_task(self, task_id: str) -> Task:
        task = await self.require_task(task_id)
        return await self._store.update_task(task.with_status(TaskStatus.CANCELLED))

    async def pause_task(self, task_id: str) -> Task:
        task = await self.require_task(task_id)
        return await self._store.update_task(task.with_status(TaskStatus.PAUSED))

    async def resume_task(self, task_id: str) -> Task:
        task = await self.require_task(task_id)
        return await self._store.update_task(task.with_status(TaskStatus.RUNNING))

    async def list_tasks(self, *, parent_task_id: str | None = None) -> list[Task]:
        return await self._store.list_tasks(parent_task_id=parent_task_id)

    async def get_task(self, task_id: str) -> Task | None:
        return await self._store.get_task(task_id)

    async def get_task_run(self, run_id: str) -> TaskRun | None:
        return await self._store.get_run(run_id)

    async def list_task_runs(self, task_id: str) -> list[TaskRun]:
        await self.require_task(task_id)
        return await self._store.list_runs(task_id)

    async def list_child_tasks(self, parent_task_id: str) -> list[Task]:
        return await self._store.list_tasks(parent_task_id=parent_task_id)

    async def summarize_child_statuses(self, parent_task_id: str) -> dict[str, int]:
        children = await self.list_child_tasks(parent_task_id)
        summary: dict[str, int] = {}
        for child in children:
            summary[child.status] = summary.get(child.status, 0) + 1
        return summary

    async def require_task(self, task_id: str) -> Task:
        task = await self._store.get_task(task_id)
        if task is None:
            raise ValueError(f"task {task_id!r} not found")
        return task

    async def require_run(self, run_id: str) -> TaskRun:
        run = await self._store.get_run(run_id)
        if run is None:
            raise ValueError(f"task run {run_id!r} not found")
        return run

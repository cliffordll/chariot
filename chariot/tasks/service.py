"""Service skeletons for task management.

These services contain workflow rules that should remain stable even if the
underlying storage moves from in-memory stubs to SQL-backed repos later.
"""

from __future__ import annotations

from typing import Protocol

from chariot.tasks.models import (
    AgentProfile,
    JobRunRecord,
    ScheduledJob,
    Task,
    TaskCreate,
    TaskKind,
    TaskRun,
    TaskRunCreate,
    TaskRunStatus,
    TaskStatus,
)


class AgentProfileStore(Protocol):
    async def list_profiles(self) -> list[AgentProfile]: ...

    async def get_profile(self, name: str) -> AgentProfile | None: ...

    async def create_agent_profile(
        self,
        *,
        name: str,
        role: str,
        prompt_bundle: str | None = None,
        tool_profile: str | None = None,
        provider_profile: str | None = None,
        budget: dict[str, object] | None = None,
        meta: dict[str, object] | None = None,
    ) -> AgentProfile: ...

    async def update_agent_profile(
        self,
        *,
        name: str,
        role: str | None = None,
        prompt_bundle: str | None = None,
        tool_profile: str | None = None,
        provider_profile: str | None = None,
        budget: dict[str, object] | None = None,
        meta: dict[str, object] | None = None,
    ) -> AgentProfile: ...

    async def delete_agent_profile(self, name: str) -> None: ...


class TaskStore(Protocol):
    async def create_task(self, spec: TaskCreate) -> Task: ...

    async def update_task(self, task: Task) -> Task: ...

    async def get_task(self, task_id: str) -> Task | None: ...

    async def list_tasks(self, *, parent_task_id: str | None = None) -> list[Task]: ...

    async def create_run(self, spec: TaskRunCreate) -> TaskRun: ...

    async def update_run(self, run: TaskRun) -> TaskRun: ...

    async def get_run(self, run_id: str) -> TaskRun | None: ...

    async def list_runs(self, task_id: str) -> list[TaskRun]: ...


class JobStore(Protocol):
    async def list_jobs(self) -> list[ScheduledJob]: ...

    async def get_job(self, name: str) -> ScheduledJob | None: ...

    async def create_job(
        self,
        *,
        name: str,
        goal: str,
        cron: str,
        enabled: bool = True,
        agent_profile: str | None = None,
        meta: dict[str, object] | None = None,
    ) -> ScheduledJob: ...

    async def update_job(
        self,
        *,
        name: str,
        goal: str | None = None,
        cron: str | None = None,
        agent_profile: str | None = None,
        meta: dict[str, object] | None = None,
    ) -> ScheduledJob: ...

    async def set_job_enabled(self, name: str, enabled: bool) -> ScheduledJob: ...

    async def delete_job(self, name: str) -> None: ...

    async def list_job_runs(self, job_name: str) -> list[JobRunRecord]: ...

    async def create_job_run(
        self,
        *,
        job_name: str,
        task_id: str | None = None,
        status: str = "queued",
        error: str | None = None,
    ) -> JobRunRecord: ...


class AgentService:
    def __init__(self, store: AgentProfileStore) -> None:
        self._store = store

    async def list_agents(self) -> list[AgentProfile]:
        return await self._store.list_profiles()

    async def get_agent(self, name: str) -> AgentProfile | None:
        return await self._store.get_profile(name)

    async def create_agent(
        self,
        *,
        name: str,
        role: str,
        prompt_bundle: str | None = None,
        tool_profile: str | None = None,
        provider_profile: str | None = None,
        budget: dict[str, object] | None = None,
        meta: dict[str, object] | None = None,
    ) -> AgentProfile:
        return await self._store.create_agent_profile(
            name=name,
            role=role,
            prompt_bundle=prompt_bundle,
            tool_profile=tool_profile,
            provider_profile=provider_profile,
            budget=budget,
            meta=meta,
        )

    async def update_agent(
        self,
        *,
        name: str,
        role: str | None = None,
        prompt_bundle: str | None = None,
        tool_profile: str | None = None,
        provider_profile: str | None = None,
        budget: dict[str, object] | None = None,
        meta: dict[str, object] | None = None,
    ) -> AgentProfile:
        agent = await self._store.get_profile(name)
        if agent is None:
            raise ValueError(f"agent profile {name!r} not found")
        if (
            role is None
            and prompt_bundle is None
            and tool_profile is None
            and provider_profile is None
            and budget is None
            and meta is None
        ):
            raise ValueError("no agent fields provided to update")
        return await self._store.update_agent_profile(
            name=name,
            role=role,
            prompt_bundle=prompt_bundle,
            tool_profile=tool_profile,
            provider_profile=provider_profile,
            budget=budget,
            meta=meta,
        )

    async def delete_agent(self, name: str) -> None:
        agent = await self._store.get_profile(name)
        if agent is None:
            raise ValueError(f"agent profile {name!r} not found")
        await self._store.delete_agent_profile(name)


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

    async def start_task_run(self, spec: TaskRunCreate) -> TaskRun:
        task = await self.require_task(spec.task_id)
        if task.status == TaskStatus.PAUSED:
            await self._store.update_task(task.with_status(TaskStatus.RUNNING))
        elif task.status == TaskStatus.QUEUED:
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


class JobService:
    def __init__(self, store: JobStore, task_service: TaskService) -> None:
        self._store = store
        self._tasks = task_service

    async def list_jobs(self) -> list[ScheduledJob]:
        return await self._store.list_jobs()

    async def get_job(self, name: str) -> ScheduledJob | None:
        return await self._store.get_job(name)

    async def list_job_runs(self, job_name: str) -> list[JobRunRecord]:
        return await self._store.list_job_runs(job_name)

    async def create_job(
        self,
        *,
        name: str,
        goal: str,
        cron: str,
        enabled: bool = True,
        agent_profile: str | None = None,
        meta: dict[str, object] | None = None,
    ) -> ScheduledJob:
        return await self._store.create_job(
            name=name,
            goal=goal,
            cron=cron,
            enabled=enabled,
            agent_profile=agent_profile,
            meta=meta,
        )

    async def update_job(
        self,
        *,
        name: str,
        goal: str | None = None,
        cron: str | None = None,
        agent_profile: str | None = None,
        meta: dict[str, object] | None = None,
    ) -> ScheduledJob:
        job = await self._store.get_job(name)
        if job is None:
            raise ValueError(f"job {name!r} not found")
        if goal is None and cron is None and agent_profile is None and meta is None:
            raise ValueError("no job fields provided to update")
        return await self._store.update_job(
            name=name,
            goal=goal,
            cron=cron,
            agent_profile=agent_profile,
            meta=meta,
        )

    async def enable_job(self, name: str) -> ScheduledJob:
        job = await self._store.get_job(name)
        if job is None:
            raise ValueError(f"job {name!r} not found")
        return await self._store.set_job_enabled(name, True)

    async def disable_job(self, name: str) -> ScheduledJob:
        job = await self._store.get_job(name)
        if job is None:
            raise ValueError(f"job {name!r} not found")
        return await self._store.set_job_enabled(name, False)

    async def delete_job(self, name: str) -> None:
        job = await self._store.get_job(name)
        if job is None:
            raise ValueError(f"job {name!r} not found")
        await self._store.delete_job(name)

    async def run_job_now(self, name: str) -> tuple[Task, JobRunRecord]:
        job = await self._store.get_job(name)
        if job is None:
            raise ValueError(f"job {name!r} not found")
        if not job.enabled:
            raise ValueError(f"job {name!r} is disabled")
        task = await self._tasks.create_task(
            TaskCreate(
                goal=job.goal,
                kind=TaskKind.SCHEDULED,
                agent_profile=job.agent_profile,
                meta={"job_name": job.name},
            )
        )
        run = await self._store.create_job_run(job_name=job.name, task_id=task.id, status="queued")
        return task, run

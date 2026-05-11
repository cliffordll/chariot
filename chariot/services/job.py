"""Scheduled job domain service."""

from __future__ import annotations

from typing import Protocol

from chariot.models.job import JobRunRecord, ScheduledJob
from chariot.models.task import Task, TaskCreate, TaskKind
from chariot.services.task import TaskService


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

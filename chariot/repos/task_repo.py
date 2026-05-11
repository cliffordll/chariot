"""TaskRepo: agent/task/job persistence for Milestone A6."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.config import ConfigError
from chariot.database.models import AgentProfileRow, JobRunRow, ScheduledJobRow, TaskRow, TaskRunRow
from chariot.models.agent import UNSET, AgentProfile, ClearableStr, _UnsetType
from chariot.models.job import JobRunRecord, ScheduledJob
from chariot.models.task import Task, TaskCreate, TaskRun, TaskRunCreate


class TaskRepo:
    """Persistence adapter for `agent_profiles`, `tasks`, `task_runs`, and `scheduled_jobs`."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_profiles(self) -> list[AgentProfile]:
        return await self.list_agent_profiles()

    async def get_profile(self, name: str) -> AgentProfile | None:
        return await self.get_agent_profile(name)

    async def list_agent_profiles(self) -> list[AgentProfile]:
        rows = (await self.session.execute(select(AgentProfileRow).order_by(AgentProfileRow.name))).scalars().all()
        return [self._row_to_agent_profile(row) for row in rows]

    async def get_agent_profile(self, name: str) -> AgentProfile | None:
        row = await self.session.get(AgentProfileRow, name)
        return self._row_to_agent_profile(row) if row is not None else None

    async def create_agent_profile(
        self,
        *,
        name: str,
        role: str,
        prompt_bundle: str | None = None,
        tool_profile: str | None = None,
        provider_profile: str | None = None,
        budget: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        reflection_enabled: bool = False,
        reflection_max_retries: int = 2,
    ) -> AgentProfile:
        self._require_non_empty(name, "agent profile name")
        self._require_non_empty(role, "agent profile role")
        if reflection_max_retries < 0:
            raise ConfigError(f"reflection_max_retries 必须 >= 0,got {reflection_max_retries}")
        row = AgentProfileRow(
            name=name,
            role=role,
            prompt_bundle=prompt_bundle,
            tool_profile=tool_profile,
            provider_profile=provider_profile,
            budget=self._serialize_object("budget", budget or {}),
            meta=self._serialize_object("meta", meta or {}),
            reflection_enabled=1 if reflection_enabled else 0,
            reflection_max_retries=reflection_max_retries,
        )
        self.session.add(row)
        try:
            await self.session.commit()
        except IntegrityError as e:
            await self.session.rollback()
            raise ConfigError(f"agent profile {name!r} 已存在") from e
        await self.session.refresh(row)
        return self._row_to_agent_profile(row)

    async def list_tasks(self, *, parent_task_id: str | None = None) -> list[Task]:
        stmt = select(TaskRow)
        if parent_task_id is not None:
            stmt = stmt.where(TaskRow.parent_task_id == parent_task_id)
        stmt = stmt.order_by(TaskRow.created_at.desc(), TaskRow.id.desc())
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._row_to_task(row) for row in rows]

    async def update_agent_profile(
        self,
        *,
        name: str,
        role: str | None = None,
        prompt_bundle: ClearableStr = UNSET,
        tool_profile: ClearableStr = UNSET,
        provider_profile: ClearableStr = UNSET,
        budget: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        reflection_enabled: bool | None = None,
        reflection_max_retries: int | None = None,
    ) -> AgentProfile:
        row = await self._require_agent_profile_row(name)
        if role is not None:
            self._require_non_empty(role, "agent profile role")
            row.role = role
        if not isinstance(prompt_bundle, _UnsetType):
            row.prompt_bundle = prompt_bundle
        if not isinstance(tool_profile, _UnsetType):
            row.tool_profile = tool_profile
        if not isinstance(provider_profile, _UnsetType):
            row.provider_profile = provider_profile
        if budget is not None:
            row.budget = self._serialize_object("budget", budget)
        if meta is not None:
            row.meta = self._serialize_object("meta", meta)
        if reflection_enabled is not None:
            row.reflection_enabled = 1 if reflection_enabled else 0
        if reflection_max_retries is not None:
            if reflection_max_retries < 0:
                raise ConfigError(f"reflection_max_retries 必须 >= 0,got {reflection_max_retries}")
            row.reflection_max_retries = reflection_max_retries
        await self.session.commit()
        await self.session.refresh(row)
        return self._row_to_agent_profile(row)

    async def delete_agent_profile(self, name: str) -> None:
        row = await self._require_agent_profile_row(name)
        await self.session.delete(row)
        await self.session.commit()

    async def get_task(self, task_id: str) -> Task | None:
        row = await self.session.get(TaskRow, task_id)
        return self._row_to_task(row) if row is not None else None

    async def create_task(self, spec: TaskCreate) -> Task:
        self._require_non_empty(spec.goal, "task goal")
        row = TaskRow(
            goal=spec.goal,
            kind=spec.kind.value,
            status="queued",
            agent_profile=spec.agent_profile,
            parent_task_id=spec.parent_task_id,
            owner=spec.owner,
            meta=self._serialize_object("meta", spec.meta),
            artifacts=self._serialize_list("artifacts", spec.artifacts),
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return self._row_to_task(row)

    async def update_task(self, task: Task) -> Task:
        row = await self._require_task_row(task.id)
        row.goal = task.goal
        row.kind = task.kind.value
        row.status = task.status.value
        row.agent_profile = task.agent_profile
        row.parent_task_id = task.parent_task_id
        row.owner = task.owner
        row.meta = self._serialize_object("meta", task.meta)
        row.artifacts = self._serialize_list("artifacts", task.artifacts)
        await self.session.commit()
        await self.session.refresh(row)
        return self._row_to_task(row)

    async def create_run(self, spec: TaskRunCreate) -> TaskRun:
        row = TaskRunRow(
            task_id=spec.task_id,
            status="running",
            trigger=spec.trigger,
            resume_from_run_id=spec.resume_from_run_id,
            meta=self._serialize_object("meta", spec.meta),
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return self._row_to_run(row)

    async def update_run(self, run: TaskRun) -> TaskRun:
        row = await self._require_run_row(run.id)
        row.task_id = run.task_id
        row.status = run.status.value
        row.trigger = run.trigger
        row.resume_from_run_id = run.resume_from_run_id
        row.result = self._serialize_object("result", run.result)
        row.error = run.error
        row.meta = self._serialize_object("meta", run.meta)
        row.started_at = run.started_at
        row.finished_at = run.finished_at
        await self.session.commit()
        await self.session.refresh(row)
        return self._row_to_run(row)

    async def get_run(self, run_id: str) -> TaskRun | None:
        row = await self.session.get(TaskRunRow, run_id)
        return self._row_to_run(row) if row is not None else None

    async def list_runs(self, task_id: str) -> list[TaskRun]:
        stmt = select(TaskRunRow).where(TaskRunRow.task_id == task_id).order_by(TaskRunRow.started_at.desc())
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._row_to_run(row) for row in rows]

    async def list_jobs(self) -> list[ScheduledJob]:
        rows = (await self.session.execute(select(ScheduledJobRow).order_by(ScheduledJobRow.name))).scalars().all()
        return [self._row_to_job(row) for row in rows]

    async def get_job(self, name: str) -> ScheduledJob | None:
        row = await self.session.get(ScheduledJobRow, name)
        return self._row_to_job(row) if row is not None else None

    async def create_job(
        self,
        *,
        name: str,
        goal: str,
        cron: str,
        enabled: bool = True,
        agent_profile: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ScheduledJob:
        self._require_non_empty(name, "job name")
        self._require_non_empty(goal, "job goal")
        self._require_non_empty(cron, "job cron")
        row = ScheduledJobRow(
            name=name,
            goal=goal,
            cron=cron,
            enabled=1 if enabled else 0,
            agent_profile=agent_profile,
            meta=self._serialize_object("meta", meta or {}),
        )
        self.session.add(row)
        try:
            await self.session.commit()
        except IntegrityError as e:
            await self.session.rollback()
            raise ConfigError(f"job {name!r} 已存在") from e
        await self.session.refresh(row)
        return self._row_to_job(row)

    async def update_job(
        self,
        *,
        name: str,
        goal: str | None = None,
        cron: str | None = None,
        agent_profile: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ScheduledJob:
        row = await self._require_job_row(name)
        if goal is not None:
            self._require_non_empty(goal, "job goal")
            row.goal = goal
        if cron is not None:
            self._require_non_empty(cron, "job cron")
            row.cron = cron
        if agent_profile is not None:
            row.agent_profile = agent_profile
        if meta is not None:
            row.meta = self._serialize_object("meta", meta)
        await self.session.commit()
        await self.session.refresh(row)
        return self._row_to_job(row)

    async def set_job_enabled(self, name: str, enabled: bool) -> ScheduledJob:
        row = await self._require_job_row(name)
        row.enabled = 1 if enabled else 0
        await self.session.commit()
        await self.session.refresh(row)
        return self._row_to_job(row)

    async def delete_job(self, name: str) -> None:
        row = await self._require_job_row(name)
        await self.session.delete(row)
        await self.session.commit()

    async def list_job_runs(self, job_name: str) -> list[JobRunRecord]:
        stmt = select(JobRunRow).where(JobRunRow.job_name == job_name).order_by(JobRunRow.started_at.desc())
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._row_to_job_run(row) for row in rows]

    async def create_job_run(
        self,
        *,
        job_name: str,
        task_id: str | None = None,
        status: str = "queued",
        error: str | None = None,
        finished_at: datetime | None = None,
    ) -> JobRunRecord:
        row = JobRunRow(
            job_name=job_name,
            task_id=task_id,
            status=status,
            error=error,
            finished_at=finished_at,
        )
        self.session.add(row)
        await self._touch_job_status(job_name, status=status)
        await self.session.commit()
        await self.session.refresh(row)
        return self._row_to_job_run(row)

    async def _require_task_row(self, task_id: str) -> TaskRow:
        row = await self.session.get(TaskRow, task_id)
        if row is None:
            raise ConfigError(f"task {task_id!r} not found")
        return row

    async def _require_agent_profile_row(self, name: str) -> AgentProfileRow:
        row = await self.session.get(AgentProfileRow, name)
        if row is None:
            raise ConfigError(f"agent profile {name!r} not found")
        return row

    async def _require_run_row(self, run_id: str) -> TaskRunRow:
        row = await self.session.get(TaskRunRow, run_id)
        if row is None:
            raise ConfigError(f"task run {run_id!r} not found")
        return row

    async def _require_job_row(self, name: str) -> ScheduledJobRow:
        row = await self.session.get(ScheduledJobRow, name)
        if row is None:
            raise ConfigError(f"job {name!r} not found")
        return row

    async def _touch_job_status(self, job_name: str, *, status: str) -> None:
        row = await self.session.get(ScheduledJobRow, job_name)
        if row is None:
            raise ConfigError(f"job {job_name!r} not found")
        row.last_run_status = status
        row.last_run_at = datetime.now(UTC)

    @staticmethod
    def _require_non_empty(value: str, label: str) -> None:
        if not value:
            raise ConfigError(f"{label} 必须是非空字符串")

    @staticmethod
    def _serialize_object(label: str, data: dict[str, Any]) -> str:
        try:
            return json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            raise ConfigError(f"task {label} 不可 JSON 序列化: {e}") from e

    @staticmethod
    def _serialize_list(label: str, data: list[str]) -> str:
        try:
            return json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            raise ConfigError(f"task {label} 不可 JSON 序列化: {e}") from e

    @staticmethod
    def _deserialize_object(label: str, raw: str) -> dict[str, Any]:
        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ConfigError(f"task {label} JSON 损坏: {e}") from e
        if not isinstance(data, dict):
            raise ConfigError(f"task {label} JSON 顶层必须是 object")
        return cast(dict[str, Any], data)

    @staticmethod
    def _deserialize_list(label: str, raw: str) -> list[str]:
        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ConfigError(f"task {label} JSON 损坏: {e}") from e
        if not isinstance(data, list) or any(not isinstance(item, str) for item in data):
            raise ConfigError(f"task {label} JSON 顶层必须是 string list")
        return cast(list[str], data)

    @classmethod
    def _row_to_agent_profile(cls, row: AgentProfileRow) -> AgentProfile:
        return AgentProfile(
            name=row.name,
            role=row.role,
            prompt_bundle=row.prompt_bundle,
            tool_profile=row.tool_profile,
            provider_profile=row.provider_profile,
            budget=cls._deserialize_object("budget", row.budget),
            meta=cls._deserialize_object("meta", row.meta),
            reflection_enabled=bool(row.reflection_enabled),
            reflection_max_retries=row.reflection_max_retries,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @classmethod
    def _row_to_task(cls, row: TaskRow) -> Task:
        from chariot.models.task import TaskKind, TaskStatus

        return Task(
            id=row.id,
            goal=row.goal,
            kind=TaskKind(row.kind),
            status=TaskStatus(row.status),
            agent_profile=row.agent_profile,
            parent_task_id=row.parent_task_id,
            owner=row.owner,
            meta=cls._deserialize_object("meta", row.meta),
            artifacts=cls._deserialize_list("artifacts", row.artifacts),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @classmethod
    def _row_to_run(cls, row: TaskRunRow) -> TaskRun:
        from chariot.models.task import TaskRunStatus

        return TaskRun(
            id=row.id,
            task_id=row.task_id,
            status=TaskRunStatus(row.status),
            trigger=row.trigger,
            resume_from_run_id=row.resume_from_run_id,
            result=cls._deserialize_object("result", row.result),
            error=row.error,
            meta=cls._deserialize_object("meta", row.meta),
            started_at=row.started_at,
            finished_at=row.finished_at,
        )

    @classmethod
    def _row_to_job(cls, row: ScheduledJobRow) -> ScheduledJob:
        return ScheduledJob(
            name=row.name,
            goal=row.goal,
            cron=row.cron,
            enabled=bool(row.enabled),
            agent_profile=row.agent_profile,
            last_run_status=row.last_run_status,
            last_run_at=row.last_run_at,
            next_run_at=row.next_run_at,
            meta=cls._deserialize_object("meta", row.meta),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _row_to_job_run(row: JobRunRow) -> JobRunRecord:
        return JobRunRecord(
            id=row.id,
            job_name=row.job_name,
            task_id=row.task_id,
            status=row.status,
            error=row.error,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )

"""TaskRepo: agent/task/job persistence for Milestone A6."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.config import ConfigError
from chariot.database.models import (
    AgentProfileRow,
    ContextBundleRow,
    ConversationRow,
    JobRunRow,
    PromptBundleRow,
    ProviderRow,
    ScheduledJobRow,
    TaskRow,
    TaskRunRow,
    ToolsetRow,
)
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
        row = await self._find_agent_profile_row(name)
        return self._row_to_agent_profile(row) if row is not None else None

    async def create_agent_profile(
        self,
        *,
        name: str,
        role: str,
        prompt_id: str | None = None,
        context_id: str | None = None,
        toolset_id: str | None = None,
        provider_id: str | None = None,
        budget: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        reflection_enabled: bool = False,
        reflection_max_retries: int = 2,
        default_skill: str | None = None,
    ) -> AgentProfile:
        self._require_non_empty(name, "agent profile name")
        self._require_non_empty(role, "agent profile role")
        if reflection_max_retries < 0:
            raise ConfigError(f"reflection_max_retries 必须 >= 0,got {reflection_max_retries}")
        prompt_id = await self._resolve_prompt_id(prompt_id)
        context_id = await self._resolve_context_id(context_id)
        toolset_id = await self._resolve_toolset_id(toolset_id)
        row = AgentProfileRow(
            name=name,
            role=role,
            prompt_id=prompt_id,
            context_id=context_id,
            toolset_id=toolset_id,
            provider_id=await self._resolve_provider_ref(provider_id),
            budget=self._serialize_object("budget", budget or {}),
            meta=self._serialize_object("meta", meta or {}),
            reflection_enabled=1 if reflection_enabled else 0,
            reflection_max_retries=reflection_max_retries,
            default_skill=default_skill,
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
        prompt_id: ClearableStr = UNSET,
        context_id: ClearableStr = UNSET,
        toolset_id: ClearableStr = UNSET,
        provider_id: ClearableStr = UNSET,
        budget: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        reflection_enabled: bool | None = None,
        reflection_max_retries: int | None = None,
        default_skill: ClearableStr = UNSET,
    ) -> AgentProfile:
        row = await self._require_agent_profile_row(name)
        if role is not None:
            self._require_non_empty(role, "agent profile role")
            row.role = role
        if not isinstance(prompt_id, _UnsetType):
            row.prompt_id = await self._resolve_prompt_id(prompt_id)
        if not isinstance(context_id, _UnsetType):
            row.context_id = await self._resolve_context_id(context_id)
        if not isinstance(toolset_id, _UnsetType):
            row.toolset_id = await self._resolve_toolset_id(toolset_id)
        if not isinstance(provider_id, _UnsetType):
            row.provider_id = await self._resolve_provider_ref(provider_id)
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
        if not isinstance(default_skill, _UnsetType):
            row.default_skill = default_skill
        await self.session.commit()
        await self.session.refresh(row)
        return self._row_to_agent_profile(row)

    async def delete_agent_profile(self, name: str) -> None:
        row = await self._require_agent_profile_row(name)
        await self.session.delete(row)
        await self.session.commit()

    async def rename_agent_profile(self, ref: str, *, new_name: str) -> AgentProfile:
        row = await self._require_agent_profile_row(ref)
        self._require_non_empty(new_name, "agent profile name")
        old_name = row.name
        row.name = new_name
        await self.session.flush()
        if old_name != new_name:
            await self.session.execute(
                update(TaskRow).where(TaskRow.agent_profile_id == row.id).values(agent_profile=new_name)
            )
            await self.session.execute(
                update(ScheduledJobRow).where(ScheduledJobRow.agent_profile_id == row.id).values(agent_profile=new_name)
            )
            await self.session.execute(
                update(ConversationRow).where(ConversationRow.agent_profile == old_name).values(agent_profile=new_name)
            )
        try:
            await self.session.commit()
        except IntegrityError as e:
            await self.session.rollback()
            raise ConfigError(f"agent profile {new_name!r} 已存在") from e
        await self.session.refresh(row)
        return self._row_to_agent_profile(row)

    async def get_task(self, task_id: str) -> Task | None:
        row = await self.session.get(TaskRow, task_id)
        return self._row_to_task(row) if row is not None else None

    async def create_task(self, spec: TaskCreate) -> Task:
        self._require_non_empty(spec.goal, "task goal")
        agent_profile_id, agent_profile_name = await self._resolve_agent_profile_ref(spec.agent_profile)
        row = TaskRow(
            goal=spec.goal,
            kind=spec.kind.value,
            status="queued",
            agent_profile=agent_profile_name,
            agent_profile_id=agent_profile_id,
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
        agent_profile_id, agent_profile_name = await self._resolve_agent_profile_ref(task.agent_profile)
        row.goal = task.goal
        row.kind = task.kind.value
        row.status = task.status.value
        row.agent_profile = agent_profile_name
        row.agent_profile_id = agent_profile_id
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
        row = await self._find_job_row(name)
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
        agent_profile_id, agent_profile_name = await self._resolve_agent_profile_ref(agent_profile)
        row = ScheduledJobRow(
            name=name,
            goal=goal,
            cron=cron,
            enabled=1 if enabled else 0,
            agent_profile=agent_profile_name,
            agent_profile_id=agent_profile_id,
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
        resolved_agent_profile_id: str | None = row.agent_profile_id
        resolved_agent_profile_name: str | None = row.agent_profile
        if goal is not None:
            self._require_non_empty(goal, "job goal")
            row.goal = goal
        if cron is not None:
            self._require_non_empty(cron, "job cron")
            row.cron = cron
        if agent_profile is not None:
            resolved_agent_profile_id, resolved_agent_profile_name = await self._resolve_agent_profile_ref(
                agent_profile
            )
        row.agent_profile = resolved_agent_profile_name
        row.agent_profile_id = resolved_agent_profile_id
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
        job = await self._require_job_row(job_name)
        stmt = (
            select(JobRunRow)
            .where((JobRunRow.job_name == job.name) | (JobRunRow.job_id == job.id))
            .order_by(JobRunRow.started_at.desc())
        )
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
        job = await self._require_job_row(job_name)
        row = JobRunRow(
            job_name=job.name,
            job_id=job.id,
            task_id=task_id,
            status=status,
            error=error,
            finished_at=finished_at,
        )
        self.session.add(row)
        await self._touch_job_status(job.name, status=status)
        await self.session.commit()
        await self.session.refresh(row)
        return self._row_to_job_run(row)

    async def _require_task_row(self, task_id: str) -> TaskRow:
        row = await self.session.get(TaskRow, task_id)
        if row is None:
            raise ConfigError(f"task {task_id!r} not found")
        return row

    async def _require_agent_profile_row(self, name: str) -> AgentProfileRow:
        row = await self._find_agent_profile_row(name)
        if row is None:
            raise ConfigError(f"agent profile {name!r} not found")
        return row

    async def _find_agent_profile_row(self, ref: str) -> AgentProfileRow | None:
        rows = await self._find_agent_profile_rows(ref)
        if not rows:
            return None
        if len(rows) == 1:
            return rows[0]
        exact_id = [row for row in rows if row.id == ref]
        if len(exact_id) == 1:
            return exact_id[0]
        exact_name = [row for row in rows if row.name == ref]
        if len(exact_name) == 1:
            return exact_name[0]
        raise ConfigError(f"agent profile 引用 {ref!r} 不唯一,请改用 id")

    async def _find_agent_profile_rows(self, ref: str) -> list[AgentProfileRow]:
        stmt = select(AgentProfileRow).where((AgentProfileRow.name == ref) | (AgentProfileRow.id == ref))
        return list((await self.session.execute(stmt)).scalars().all())

    async def _resolve_agent_profile_ref(self, ref: str | None) -> tuple[str | None, str | None]:
        if ref is None:
            return None, None
        row = await self._find_agent_profile_row(ref)
        if row is None:
            return None, ref
        return row.id, row.name

    async def _resolve_toolset_id(self, toolset_id: str | None) -> str | None:
        if toolset_id is None:
            return None
        stmt = select(ToolsetRow).where(ToolsetRow.id == toolset_id)
        rows = (await self.session.execute(stmt)).scalars().all()
        if not rows:
            return toolset_id
        if len(rows) == 1:
            return rows[0].id
        exact_id = [row for row in rows if row.id == toolset_id]
        if len(exact_id) == 1:
            return exact_id[0].id
        raise ConfigError(f"toolset id 引用 {toolset_id!r} 不唯一")

    async def _resolve_prompt_id(self, prompt_id: str | None) -> str | None:
        if prompt_id is None:
            return None
        stmt = select(PromptBundleRow).where(PromptBundleRow.id == prompt_id)
        rows = (await self.session.execute(stmt)).scalars().all()
        if not rows:
            return prompt_id
        if len(rows) == 1:
            return rows[0].id
        exact_id = [row for row in rows if row.id == prompt_id]
        if len(exact_id) == 1:
            return exact_id[0].id
        raise ConfigError(f"prompt id 引用 {prompt_id!r} 不唯一")

    async def _resolve_context_id(self, context_id: str | None) -> str | None:
        if context_id is None:
            return None
        stmt = select(ContextBundleRow).where(ContextBundleRow.id == context_id)
        rows = (await self.session.execute(stmt)).scalars().all()
        if not rows:
            return context_id
        if len(rows) == 1:
            return rows[0].id
        exact_id = [row for row in rows if row.id == context_id]
        if len(exact_id) == 1:
            return exact_id[0].id
        raise ConfigError(f"context id 引用 {context_id!r} 不唯一")

    async def _require_run_row(self, run_id: str) -> TaskRunRow:
        row = await self.session.get(TaskRunRow, run_id)
        if row is None:
            raise ConfigError(f"task run {run_id!r} not found")
        return row

    async def _require_job_row(self, name: str) -> ScheduledJobRow:
        row = await self._find_job_row(name)
        if row is None:
            raise ConfigError(f"job {name!r} not found")
        return row

    async def _find_job_row(self, ref: str) -> ScheduledJobRow | None:
        stmt = select(ScheduledJobRow).where((ScheduledJobRow.name == ref) | (ScheduledJobRow.id == ref))
        rows = (await self.session.execute(stmt)).scalars().all()
        if not rows:
            return None
        if len(rows) == 1:
            return rows[0]
        exact_id = [row for row in rows if row.id == ref]
        if len(exact_id) == 1:
            return exact_id[0]
        exact_name = [row for row in rows if row.name == ref]
        if len(exact_name) == 1:
            return exact_name[0]
        raise ConfigError(f"job 引用 {ref!r} 不唯一,请改用 id")

    async def _touch_job_status(self, job_name: str, *, status: str) -> None:
        row = await self.session.get(ScheduledJobRow, job_name)
        if row is None:
            raise ConfigError(f"job {job_name!r} not found")
        row.last_run_status = status
        row.last_run_at = datetime.now(UTC)

    async def _resolve_provider_ref(self, ref: str | None) -> str | None:
        if ref is None:
            return None
        stmt = select(ProviderRow).where(
            (ProviderRow.id == ref) | (ProviderRow.slug == ref) | (ProviderRow.name == ref)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        if not rows:
            return ref
        if len(rows) > 1:
            exact = [row for row in rows if row.id == ref or row.slug == ref]
            if len(exact) == 1:
                return exact[0].id
            raise ConfigError(f"provider 引用 {ref!r} 不唯一,请改用 slug 或 id")
        return rows[0].id

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
        return AgentProfile.from_provider_id(
            name=row.name,
            role=row.role,
            prompt_id=row.prompt_id,
            context_id=row.context_id,
            toolset_id=row.toolset_id,
            provider_id=row.provider_id,
            budget=cls._deserialize_object("budget", row.budget),
            meta=cls._deserialize_object("meta", row.meta),
            reflection_enabled=bool(row.reflection_enabled),
            reflection_max_retries=row.reflection_max_retries,
            default_skill=row.default_skill,
            id=row.id,
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
            id=row.id,
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
            job_id=row.job_id,
            task_id=row.task_id,
            status=row.status,
            error=row.error,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )

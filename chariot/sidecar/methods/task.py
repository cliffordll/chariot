"""sidecar task/agent/job RPC adapters."""

from __future__ import annotations

from typing import Any

from chariot.rpc.jsonrpc import RpcContext
from chariot.sidecar.methods import MethodBase
from chariot.sidecar.runtime import SidecarRuntime
from chariot.sidecar.services.task import TaskManagementService


class TaskMethods(MethodBase):
    def __init__(self, runtime: SidecarRuntime) -> None:
        super().__init__(runtime)
        self._service = TaskManagementService(runtime)

    async def list_agents(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del params, ctx
        async with self._session() as session:
            agents = await self._service.list_agents(session)
        return {"agents": agents}

    async def get_agent(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        async with self._session() as session:
            agent = await self._service.get_agent(session, name=name)
        return {"agent": agent}

    async def create_agent(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        role = self._require_str(params, "role")
        prompt_bundle = self._optional_str(params, "prompt_bundle")
        tool_profile = self._optional_str(params, "tool_profile")
        provider_profile = self._optional_str(params, "provider_profile")
        budget = self._optional_dict(params, "budget")
        meta = self._optional_dict(params, "meta")
        async with self._session() as session:
            agent = await self._service.create_agent(
                session,
                name=name,
                role=role,
                prompt_bundle=prompt_bundle,
                tool_profile=tool_profile,
                provider_profile=provider_profile,
                budget=budget,
                meta=meta,
            )
        return {"agent": agent}

    async def update_agent(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        role = self._optional_str(params, "role")
        prompt_bundle = self._optional_str(params, "prompt_bundle")
        tool_profile = self._optional_str(params, "tool_profile")
        provider_profile = self._optional_str(params, "provider_profile")
        budget = self._optional_dict(params, "budget")
        meta = self._optional_dict(params, "meta")
        async with self._session() as session:
            agent = await self._service.update_agent(
                session,
                name=name,
                role=role,
                prompt_bundle=prompt_bundle,
                tool_profile=tool_profile,
                provider_profile=provider_profile,
                budget=budget,
                meta=meta,
            )
        return {"agent": agent}

    async def delete_agent(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        async with self._session() as session:
            result = await self._service.delete_agent(session, name=name)
        return result

    async def list_tasks(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        parent_task_id = self._optional_str(params, "parent_task_id")
        async with self._session() as session:
            tasks = await self._service.list_tasks(session, parent_task_id=parent_task_id)
        return {"tasks": tasks}

    async def get_task(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        task_id = self._require_str(params, "task_id")
        async with self._session() as session:
            task = await self._service.get_task(session, task_id=task_id)
        return {"task": task}

    async def get_task_run(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        run_id = self._require_str(params, "run_id")
        async with self._session() as session:
            run = await self._service.get_task_run(session, run_id=run_id)
        return {"run": run}

    async def list_task_runs(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        task_id = self._require_str(params, "task_id")
        async with self._session() as session:
            runs = await self._service.list_task_runs(session, task_id=task_id)
        return {"runs": runs}

    async def list_jobs(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del params, ctx
        async with self._session() as session:
            jobs = await self._service.list_jobs(session)
        return {"jobs": jobs}

    async def show_job(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        async with self._session() as session:
            job = await self._service.get_job(session, name=name)
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
            job = await self._service.create_job(
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
            job = await self._service.update_job(
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
            job = await self._service.enable_job(session, name=name)
        return {"job": job}

    async def disable_job(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        async with self._session() as session:
            job = await self._service.disable_job(session, name=name)
        return {"job": job}

    async def delete_job(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        async with self._session() as session:
            result = await self._service.delete_job(session, name=name)
        return result

    async def run_job_now(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        name = self._require_str(params, "name")
        async with self._session() as session:
            result = await self._service.run_job_now(session, name=name)
        return result

    async def create(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        goal = self._require_str(params, "goal")
        kind = self._optional_str(params, "kind") or "interactive"
        agent_profile = self._optional_str(params, "agent_profile")
        owner = self._optional_str(params, "owner")
        meta = self._optional_dict(params, "meta")
        async with self._session() as session:
            task = await self._service.create_task(
                session,
                goal=goal,
                kind=kind,
                agent_profile=agent_profile,
                owner=owner,
                meta=meta,
            )
        return {"task": task}

    async def pause(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        task_id = self._require_str(params, "task_id")
        async with self._session() as session:
            task = await self._service.pause_task(session, task_id=task_id)
        return {"task": task}

    async def resume(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        task_id = self._require_str(params, "task_id")
        async with self._session() as session:
            task = await self._service.resume_task(session, task_id=task_id)
        return {"task": task}

    async def cancel(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        task_id = self._require_str(params, "task_id")
        async with self._session() as session:
            task = await self._service.cancel_task(session, task_id=task_id)
        return {"task": task}

    async def start_run(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        task_id = self._require_str(params, "task_id")
        trigger = self._optional_str(params, "trigger") or "manual"
        resume_from_run_id = self._optional_str(params, "resume_from_run_id")
        meta = self._optional_dict(params, "meta")
        async with self._session() as session:
            run = await self._service.start_task_run(
                session,
                task_id=task_id,
                trigger=trigger,
                resume_from_run_id=resume_from_run_id,
                meta=meta,
            )
        return {"run": run}

    async def complete_run(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        run_id = self._require_str(params, "run_id")
        result = self._optional_dict(params, "result")
        error = self._optional_str(params, "error")
        async with self._session() as session:
            run = await self._service.complete_task_run(session, run_id=run_id, result=result, error=error)
        return {"run": run}

    async def fail_run(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        run_id = self._require_str(params, "run_id")
        error = self._require_str(params, "error")
        result = self._optional_dict(params, "result")
        async with self._session() as session:
            run = await self._service.fail_task_run(session, run_id=run_id, error=error, result=result)
        return {"run": run}

    async def cancel_run(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        run_id = self._require_str(params, "run_id")
        error = self._optional_str(params, "error")
        result = self._optional_dict(params, "result")
        async with self._session() as session:
            run = await self._service.cancel_task_run(session, run_id=run_id, error=error, result=result)
        return {"run": run}

    async def delegate(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        parent_task_id = self._require_str(params, "parent_task_id")
        tasks = self._require_task_specs(params, "tasks")
        reason = self._optional_str(params, "reason")
        meta = self._optional_dict(params, "meta")
        async with self._session() as session:
            result = await self._service.delegate_task(
                session,
                parent_task_id=parent_task_id,
                tasks=tasks,
                reason=reason,
                meta=meta,
            )
        return {"delegation": result}

    @staticmethod
    def _require_task_specs(params: dict[str, Any], key: str) -> list[dict[str, Any]]:
        value = params.get(key)
        if not isinstance(value, list) or not value:
            raise TypeError(f"{key!r} is required and must be a non-empty list")
        out: list[dict[str, Any]] = []
        for idx, item in enumerate(value):
            if not isinstance(item, dict):
                raise TypeError(f"{key}[{idx}] must be an object")
            goal = item.get("goal")
            if not isinstance(goal, str) or not goal:
                raise TypeError(f"{key}[{idx}].goal must be a non-empty string")
            out.append(item)
        return out

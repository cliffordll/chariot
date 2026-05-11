"""Delegation workflow skeleton."""

from __future__ import annotations

from chariot.tasks.service import TaskService
from chariot.delegation.models import DelegationRequest, DelegationResult


class DelegationService:
    """Create child tasks and preserve lineage.

    This service intentionally does not call external tools. Its job is to
    express parent/child task relationships in a stable, queryable form.
    """

    def __init__(self, task_service: TaskService) -> None:
        self._tasks = task_service

    async def delegate(self, request: DelegationRequest) -> DelegationResult:
        child_ids: list[str] = []
        for spec in request.tasks:
            task = await self._tasks.create_child_task(
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
            child_ids.append(task.id)
        return DelegationResult(
            parent_task_id=request.parent_task_id,
            child_task_ids=tuple(child_ids),
            requested=len(request.tasks),
            created=len(child_ids),
        )

"""B5 wave 2 —— `AuditHookManager` 单元 + 集成。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chariot.audit.hooks import AuditHookManager
from chariot.database.session import dispose_db, init_db
from chariot.guardrails import GuardrailEngine
from chariot.guardrails.approval import ApprovalPolicy
from chariot.repos.audit_repo import AuditRepo
from chariot.tools.base import BaseTool
from chariot.tools.execution import ToolExecutionService


@pytest_asyncio.fixture
async def sessionmaker_(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    sm = await init_db(tmp_path / "audit.db")
    try:
        yield sm
    finally:
        await dispose_db()


# ---- AuditHookManager 单元 ----


async def test_disabled_hook_manager_is_noop() -> None:
    """sessionmaker=None → enabled=False,record 静默返回。"""
    hooks = AuditHookManager(None)
    assert hooks.enabled is False
    await hooks.record_tool_call_pre(tool_name="x", args={}, tool_use_id="t1")
    await hooks.record_memory_store(memory_id="m1", action="create")
    # 不抛、不写,够了


async def test_record_writes_to_audit_events(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    hooks = AuditHookManager(sessionmaker_)
    await hooks.record_tool_call_pre(
        tool_name="shell_exec",
        args={"command": "echo hi"},
        tool_use_id="t1",
    )
    async with sessionmaker_() as session:
        events = await AuditRepo(session).list_events(limit=10)
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == AuditHookManager.EVENT_TOOL_CALL_PRE
    assert ev.payload["tool_name"] == "shell_exec"
    assert ev.payload["tool_use_id"] == "t1"


async def test_record_six_event_types(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    hooks = AuditHookManager(sessionmaker_)
    await hooks.record_tool_call_pre(tool_name="t", args={}, tool_use_id="u")
    await hooks.record_tool_call_post(tool_name="t", is_error=False, duration_ms=5, tool_use_id="u")
    await hooks.record_guardrail_verdict(
        rule_id="shell_rm_rf",
        verdict="deny",
        tool_name="shell_exec",
        matched_pattern="rm -rf",
        quota_remaining=None,
        quota_exhausted=False,
    )
    await hooks.record_memory_store(memory_id="m1", action="create", kind="preference", pinned=False)
    await hooks.record_checkpoint_create(checkpoint_id="c1", name="before-edit", kind="file")
    await hooks.record_rollback(checkpoint_id="c1", ok=True, restored=["a.txt"])

    async with sessionmaker_() as session:
        events = await AuditRepo(session).list_events(limit=20)
    types = {ev.event_type for ev in events}
    assert types == {
        AuditHookManager.EVENT_TOOL_CALL_PRE,
        AuditHookManager.EVENT_TOOL_CALL_POST,
        AuditHookManager.EVENT_GUARDRAIL_VERDICT,
        AuditHookManager.EVENT_MEMORY_STORE,
        AuditHookManager.EVENT_CHECKPOINT_CREATE,
        AuditHookManager.EVENT_ROLLBACK,
    }


async def test_post_status_reflects_error_flag(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    hooks = AuditHookManager(sessionmaker_)
    await hooks.record_tool_call_post(tool_name="t", is_error=False, tool_use_id="u")
    await hooks.record_tool_call_post(tool_name="t", is_error=True, tool_use_id="u")
    async with sessionmaker_() as session:
        events = await AuditRepo(session).list_events(limit=10)
    statuses = {ev.status for ev in events if ev.event_type == AuditHookManager.EVENT_TOOL_CALL_POST}
    assert statuses == {"ok", "error"}


# ---- ToolExecutionService 集成 ----


class _EchoTool(BaseTool):
    name = "shell_exec"

    def schema(self) -> dict[str, Any]:
        return {"name": "shell_exec", "description": "x", "input_schema": {}}

    @classmethod
    def create(cls, entry):  # type: ignore[no-untyped-def]
        return cls()

    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        return {"type": "tool_result", "content": [{"type": "text", "text": "ok"}]}


async def test_tool_exec_fires_pre_and_post(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    hooks = AuditHookManager(sessionmaker_)
    svc = ToolExecutionService(
        tools={"shell_exec": _EchoTool()},
        audit_hooks=hooks,
    )
    ev = await svc.execute_tool_call(
        tool_use_id="t1",
        tool_name="shell_exec",
        tool_input={"command": "echo hi"},
    )
    assert ev.is_error is False
    async with sessionmaker_() as session:
        events = await AuditRepo(session).list_events(limit=10)
    types = [ev.event_type for ev in events]
    # 最新事件在前(desc by created_at)
    assert AuditHookManager.EVENT_TOOL_CALL_PRE in types
    assert AuditHookManager.EVENT_TOOL_CALL_POST in types


async def test_tool_exec_records_guardrail_verdict_on_deny(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    hooks = AuditHookManager(sessionmaker_)
    svc = ToolExecutionService(
        tools={"shell_exec": _EchoTool()},
        guardrail_engine=GuardrailEngine.with_defaults(),
        audit_hooks=hooks,
    )
    ev = await svc.execute_tool_call(
        tool_use_id="t1",
        tool_name="shell_exec",
        tool_input={"command": "rm -rf /"},
    )
    assert ev.is_error is True
    async with sessionmaker_() as session:
        events = await AuditRepo(session).list_events(limit=10)
    types = [ev.event_type for ev in events]
    statuses = {ev.event_type: ev.status for ev in events}
    assert AuditHookManager.EVENT_TOOL_CALL_PRE in types
    assert AuditHookManager.EVENT_GUARDRAIL_VERDICT in types
    assert AuditHookManager.EVENT_TOOL_CALL_POST in types
    # guardrail status 反映 verdict
    assert statuses.get(AuditHookManager.EVENT_GUARDRAIL_VERDICT) == "deny"


async def test_tool_exec_records_guardrail_verdict_on_require_approval_blocked(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    hooks = AuditHookManager(sessionmaker_)
    svc = ToolExecutionService(
        tools={"shell_exec": _EchoTool()},
        guardrail_engine=GuardrailEngine.with_defaults(),
        approval_policy=ApprovalPolicy(yolo=False),
        audit_hooks=hooks,
    )
    ev = await svc.execute_tool_call(
        tool_use_id="t1",
        tool_name="shell_exec",
        tool_input={"command": "chmod 777 /opt"},
    )
    assert ev.is_error is True
    async with sessionmaker_() as session:
        events = await AuditRepo(session).list_events(limit=10)
    statuses = {ev.event_type: ev.status for ev in events}
    assert statuses.get(AuditHookManager.EVENT_GUARDRAIL_VERDICT) == "require_approval"


async def test_tool_exec_records_guardrail_verdict_on_yolo_passthrough(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    """yolo=True 时 REQUIRE_APPROVAL 放行;guardrail_verdict 仍应被记录。"""
    hooks = AuditHookManager(sessionmaker_)
    svc = ToolExecutionService(
        tools={"shell_exec": _EchoTool()},
        guardrail_engine=GuardrailEngine.with_defaults(),
        approval_policy=ApprovalPolicy(yolo=True),
        audit_hooks=hooks,
    )
    ev = await svc.execute_tool_call(
        tool_use_id="t1",
        tool_name="shell_exec",
        tool_input={"command": "chmod 777 /opt"},
    )
    assert ev.is_error is False  # yolo 放行
    async with sessionmaker_() as session:
        events = await AuditRepo(session).list_events(limit=10)
    types = [ev.event_type for ev in events]
    statuses = {ev.event_type: ev.status for ev in events}
    assert AuditHookManager.EVENT_GUARDRAIL_VERDICT in types
    assert statuses.get(AuditHookManager.EVENT_GUARDRAIL_VERDICT) == "require_approval"
    assert AuditHookManager.EVENT_TOOL_CALL_POST in types


async def test_tool_exec_without_audit_hooks_is_silent(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    """不注入 audit_hooks → 默认 disabled,不抛、不写。"""
    svc = ToolExecutionService(tools={"shell_exec": _EchoTool()})
    ev = await svc.execute_tool_call(
        tool_use_id="t1",
        tool_name="shell_exec",
        tool_input={"command": "echo hi"},
    )
    assert ev.is_error is False
    async with sessionmaker_() as session:
        events = await AuditRepo(session).list_events(limit=10)
    assert events == []


pytestmark = pytest.mark.asyncio

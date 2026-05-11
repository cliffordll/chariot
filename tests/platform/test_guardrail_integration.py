"""B5 wave 1 — ToolExecutionService + AIAgent guardrail 集成。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest_asyncio

from chariot.agent.registry import AgentRegistry
from chariot.agent.run import AIAgent
from chariot.database.session import dispose_db
from chariot.guardrails import GuardrailEngine
from chariot.guardrails.approval import ApprovalPolicy
from chariot.tools.base import BaseTool
from chariot.tools.execution import ToolExecutionService


class _AlwaysOkTool(BaseTool):
    name = "shell_exec"

    def schema(self) -> dict[str, Any]:
        return {"name": "shell_exec", "description": "x", "input_schema": {}}

    @classmethod
    def create(cls, entry):  # type: ignore[no-untyped-def]
        return cls()

    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        return {"type": "tool_result", "content": [{"type": "text", "text": "ran"}]}


# ---- ToolExecutionService 直接 ----


async def test_tool_exec_blocks_deny_pre_call() -> None:
    tool_called = {"v": False}

    class T(_AlwaysOkTool):
        async def execute(self, input):  # type: ignore[no-untyped-def]
            tool_called["v"] = True
            return await super().execute(input)

    svc = ToolExecutionService(
        tools={"shell_exec": T()},
        guardrail_engine=GuardrailEngine.with_defaults(),
    )
    ev = await svc.execute_tool_call(
        tool_use_id="t1",
        tool_name="shell_exec",
        tool_input={"command": "rm -rf /tmp/x"},
    )
    assert ev.is_error is True
    # tool 没被调
    assert tool_called["v"] is False
    # error 内容里含 rule_id
    content = ev.content
    assert isinstance(content, str)
    assert "shell_rm_rf" in content
    assert "guardrail deny" in content


async def test_tool_exec_blocks_require_approval_default_policy() -> None:
    """默认 ApprovalPolicy(yolo=False)不放 REQUIRE_APPROVAL。"""
    svc = ToolExecutionService(
        tools={"shell_exec": _AlwaysOkTool()},
        guardrail_engine=GuardrailEngine.with_defaults(),
        approval_policy=ApprovalPolicy(yolo=False),
    )
    ev = await svc.execute_tool_call(
        tool_use_id="t1",
        tool_name="shell_exec",
        tool_input={"command": "chmod 777 /opt"},
    )
    assert ev.is_error is True
    content = ev.content
    assert isinstance(content, str)
    assert "shell_chmod_unsafe" in content
    assert "guardrail require_approval" in content


async def test_tool_exec_yolo_lets_require_approval_through() -> None:
    """ApprovalPolicy(yolo=True) → REQUIRE_APPROVAL 放行,但 DENY 仍拒。"""
    svc = ToolExecutionService(
        tools={"shell_exec": _AlwaysOkTool()},
        guardrail_engine=GuardrailEngine.with_defaults(),
        approval_policy=ApprovalPolicy(yolo=True),
    )
    ok = await svc.execute_tool_call(
        tool_use_id="t1",
        tool_name="shell_exec",
        tool_input={"command": "chmod 777 /opt"},
    )
    assert ok.is_error is False
    # DENY 即使 yolo 也拒
    deny = await svc.execute_tool_call(
        tool_use_id="t2",
        tool_name="shell_exec",
        tool_input={"command": "rm -rf /"},
    )
    assert deny.is_error is True


async def test_tool_exec_allows_safe_calls() -> None:
    svc = ToolExecutionService(
        tools={"shell_exec": _AlwaysOkTool()},
        guardrail_engine=GuardrailEngine.with_defaults(),
    )
    ev = await svc.execute_tool_call(
        tool_use_id="t1",
        tool_name="shell_exec",
        tool_input={"command": "echo hello"},
    )
    assert ev.is_error is False


async def test_tool_exec_no_guardrail_backwards_compat() -> None:
    """不传 guardrail_engine → 退化为全放行(向后兼容)。"""
    svc = ToolExecutionService(tools={"shell_exec": _AlwaysOkTool()})
    ev = await svc.execute_tool_call(
        tool_use_id="t1",
        tool_name="shell_exec",
        tool_input={"command": "rm -rf /"},
    )
    assert ev.is_error is False


# ---- AIAgent bootstrap 装上 13 条规则 ----


@pytest_asyncio.fixture
async def agent(tmp_path: Path) -> AsyncIterator[AIAgent]:
    a = await AIAgent.bootstrap(tmp_path / "test.db")
    AgentRegistry._agents.clear()
    try:
        yield a
    finally:
        AgentRegistry._agents.clear()
        await dispose_db()


async def test_bootstrap_loads_guardrails(agent: AIAgent) -> None:
    engine = agent.guardrail_engine
    assert engine is not None
    assert len(engine.rules) == 13

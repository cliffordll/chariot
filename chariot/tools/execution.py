"""Stable tool execution entrypoint for AgentLoop。

B5 wave 1:在 `tool.execute(...)` 前先跑 GuardrailEngine.evaluate:
- DENY → 直接 tool_result(is_error=True),不进 tool.execute
- REQUIRE_APPROVAL + ApprovalPolicy.auto_approve(name) → 放行
- ALLOW → 正常 execute

B5 wave 2:接 `AuditHookManager` —— pre/post tool_call + guardrail_verdict 三类
事件 best-effort 写 `audit_events`,失败不阻断主链路。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, cast

from chariot.agent.chat_event import ChatEvent
from chariot.agent.exceptions import ConfigError
from chariot.guardrails import GuardrailEngine, GuardrailVerdict, Verdict
from chariot.guardrails.approval import ApprovalPolicy
from chariot.tools.custom import ShellCustomTool

if TYPE_CHECKING:
    from chariot.audit import AuditHookManager
    from chariot.tools.base import BaseTool


class ToolExecutionService:
    """Execute tool calls behind one stable call-site.

    B5 wave 1:可选注入 `guardrail_engine` + `approval_policy`;不注入则退化
    为"全放行"(向后兼容)。
    B5 wave 2:可选注入 `audit_hooks`(best-effort 写 audit_events;None 退化为
    no-op)。
    """

    def __init__(
        self,
        tools: dict[str, BaseTool],
        *,
        guardrail_engine: GuardrailEngine | None = None,
        approval_policy: ApprovalPolicy | None = None,
        audit_hooks: AuditHookManager | None = None,
        todo_store: Any | None = None,
    ) -> None:
        from chariot.audit import AuditHookManager as _AuditHookManager

        self._tools = tools
        self._guardrails = guardrail_engine
        self._approval = approval_policy or ApprovalPolicy()
        # 默认装一个 disabled hook manager,让调用点不必 None-check
        self._audit_hooks = audit_hooks or _AuditHookManager(None)
        self._todo_store = todo_store

    async def execute_tool_call(
        self,
        *,
        tool_use_id: str,
        tool_name: str,
        tool_input: Any,
    ) -> ChatEvent:
        if isinstance(tool_input, dict) and "_invalid_input_json" in tool_input:
            return ChatEvent.tool_result_event(
                tool_use_id=tool_use_id,
                content=f"invalid_json from LLM: {tool_input['_invalid_input_json']!r}",
                is_error=True,
            )

        tool = self._tools.get(tool_name)
        if tool is None:
            return ChatEvent.tool_result_event(
                tool_use_id=tool_use_id,
                content=f"unknown tool: {tool_name!r}",
                is_error=True,
            )

        safe_input: dict[str, Any] = cast(dict[str, Any], tool_input) if isinstance(tool_input, dict) else {}
        exec_input = dict(safe_input)
        if tool_name == "todo" and self._todo_store is not None and "_todo_store" not in exec_input:
            exec_input["_todo_store"] = self._todo_store

        # B5 wave 2:pre hook
        await self._audit_hooks.record_tool_call_pre(
            tool_name=tool_name,
            args=exec_input,
            tool_use_id=tool_use_id,
        )

        # B5 wave 1:guardrail pre-check
        if self._guardrails is not None:
            try:
                guardrail_tool_name, guardrail_args = self._build_guardrail_args(tool_name, tool, exec_input)
            except ConfigError as e:
                return ChatEvent.tool_result_event(
                    tool_use_id=tool_use_id,
                    content=str(e),
                    is_error=True,
                )
            verdict = self._guardrails.evaluate(tool_name=guardrail_tool_name, args=guardrail_args)
            blocked = await self._maybe_block(
                verdict,
                tool_use_id=tool_use_id,
                tool_name=tool_name,
            )
            if blocked is not None:
                await self._audit_hooks.record_tool_call_post(
                    tool_name=tool_name,
                    is_error=True,
                    duration_ms=0,
                    tool_use_id=tool_use_id,
                )
                return blocked

        start = time.monotonic()
        try:
            result_block = await tool.execute(exec_input)
        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            await self._audit_hooks.record_tool_call_post(
                tool_name=tool_name,
                is_error=True,
                duration_ms=duration_ms,
                tool_use_id=tool_use_id,
            )
            return ChatEvent.tool_result_event(
                tool_use_id=tool_use_id,
                content=f"tool {tool_name!r} 执行异常: {e}",
                is_error=True,
            )

        duration_ms = int((time.monotonic() - start) * 1000)
        is_error = bool(result_block.get("is_error", False))
        await self._audit_hooks.record_tool_call_post(
            tool_name=tool_name,
            is_error=is_error,
            duration_ms=duration_ms,
            tool_use_id=tool_use_id,
        )
        return ChatEvent.tool_result_event(
            tool_use_id=tool_use_id,
            content=result_block.get("content", []),
            is_error=is_error,
        )

    @staticmethod
    def _build_guardrail_args(
        tool_name: str,
        tool: BaseTool,
        tool_input: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        if isinstance(tool, ShellCustomTool):
            return "shell_exec", {"command": tool.render_command(tool_input)}
        return tool_name, tool_input

    async def _maybe_block(
        self,
        verdict: GuardrailVerdict,
        *,
        tool_use_id: str,
        tool_name: str,
    ) -> ChatEvent | None:
        """根据 verdict + ApprovalPolicy 决定是否拦截。返 None 表示放行。

        verdict ≠ ALLOW 时写 `guardrail_verdict` audit event(无论最终拦还是放,
        让审计能看见 REQUIRE_APPROVAL 命中后 ApprovalPolicy 的决策)。
        """
        if verdict.verdict == Verdict.ALLOW:
            return None
        # 非 ALLOW → 先记 audit
        await self._audit_hooks.record_guardrail_verdict(
            rule_id=verdict.rule_id,
            verdict=verdict.verdict.value,
            tool_name=tool_name,
            matched_pattern=verdict.matched_pattern,
            quota_remaining=verdict.quota_remaining,
            quota_exhausted=verdict.quota_exhausted,
            tool_use_id=tool_use_id,
        )
        if verdict.verdict == Verdict.REQUIRE_APPROVAL and self._approval.auto_approve(tool_name):
            return None  # wave 3 yolo / capability 放行
        return ChatEvent.tool_result_event(
            tool_use_id=tool_use_id,
            content=f"guardrail {verdict.verdict.value}: {verdict.rule_id}: {verdict.reason}",
            is_error=True,
        )

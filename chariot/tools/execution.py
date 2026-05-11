"""Stable tool execution entrypoint for AgentLoop。

B5 wave 1:在 `tool.execute(...)` 前先跑 GuardrailEngine.evaluate:
- DENY → 直接 tool_result(is_error=True),不进 tool.execute
- REQUIRE_APPROVAL + ApprovalPolicy.auto_approve(name) → 放行(audit hook 仍写,
  wave 2 才接);否则当 DENY 拒
- ALLOW → 正常 execute
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from chariot.agent.chat_event import ChatEvent
from chariot.guardrails import GuardrailEngine, GuardrailVerdict, Verdict
from chariot.guardrails.approval import ApprovalPolicy

if TYPE_CHECKING:
    from chariot.tools.base import BaseTool


class ToolExecutionService:
    """Execute tool calls behind one stable call-site.

    B5 wave 1:可选注入 `guardrail_engine` + `approval_policy`;不注入则退化
    为"全放行"(向后兼容)。
    """

    def __init__(
        self,
        tools: dict[str, BaseTool],
        *,
        guardrail_engine: GuardrailEngine | None = None,
        approval_policy: ApprovalPolicy | None = None,
    ) -> None:
        self._tools = tools
        self._guardrails = guardrail_engine
        self._approval = approval_policy or ApprovalPolicy()

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

        # B5 wave 1:guardrail pre-check
        if self._guardrails is not None:
            verdict = self._guardrails.evaluate(tool_name=tool_name, args=safe_input)
            blocked = self._maybe_block(verdict, tool_use_id=tool_use_id, tool_name=tool_name)
            if blocked is not None:
                return blocked

        try:
            result_block = await tool.execute(safe_input)
        except Exception as e:
            return ChatEvent.tool_result_event(
                tool_use_id=tool_use_id,
                content=f"tool {tool_name!r} 执行异常: {e}",
                is_error=True,
            )

        return ChatEvent.tool_result_event(
            tool_use_id=tool_use_id,
            content=result_block.get("content", []),
            is_error=bool(result_block.get("is_error", False)),
        )

    def _maybe_block(
        self,
        verdict: GuardrailVerdict,
        *,
        tool_use_id: str,
        tool_name: str,
    ) -> ChatEvent | None:
        """根据 verdict + ApprovalPolicy 决定是否拦截。返 None 表示放行。"""
        if verdict.verdict == Verdict.ALLOW:
            return None
        if verdict.verdict == Verdict.REQUIRE_APPROVAL and self._approval.auto_approve(tool_name):
            return None  # wave 3 yolo / capability 放行
        return ChatEvent.tool_result_event(
            tool_use_id=tool_use_id,
            content=f"guardrail {verdict.verdict.value}: {verdict.rule_id}: {verdict.reason}",
            is_error=True,
        )

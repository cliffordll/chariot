"""Stable tool execution entrypoint for AgentLoop."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from chariot.agent.chat_event import ChatEvent

if TYPE_CHECKING:
    from chariot.tools.base import BaseTool


class ToolExecutionService:
    """Execute tool calls behind one stable call-site."""

    def __init__(self, tools: dict[str, BaseTool]) -> None:
        self._tools = tools

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

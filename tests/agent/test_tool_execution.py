from __future__ import annotations

from typing import Any

import pytest

from chariot.tools.base import BaseTool
from chariot.tools.execution import ToolExecutionService


class _StubTool(BaseTool):
    def __init__(
        self,
        name: str,
        result: dict[str, Any] | None = None,
        exc: Exception | None = None,
    ) -> None:
        self.name = name
        self._result = result
        self._exc = exc
        self.last_input: dict[str, Any] | None = None

    @classmethod
    def create(cls, entry: Any) -> _StubTool:
        return cls("stub")

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "stub",
            "input_schema": {"type": "object", "properties": {}},
        }

    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        self.last_input = input
        if self._exc is not None:
            raise self._exc
        return self._result or {
            "type": "tool_result",
            "content": [{"type": "text", "text": "ok"}],
        }


class TestToolExecutionService:
    @pytest.mark.asyncio
    async def test_executes_known_tool(self) -> None:
        tool = _StubTool("stub")
        service = ToolExecutionService({"stub": tool})

        event = await service.execute_tool_call(
            tool_use_id="toolu_1",
            tool_name="stub",
            tool_input={"x": 1},
        )

        assert event.kind == "tool_result"
        assert event.is_error is False
        assert tool.last_input == {"x": 1}

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self) -> None:
        service = ToolExecutionService({})

        event = await service.execute_tool_call(
            tool_use_id="toolu_1",
            tool_name="ghost",
            tool_input={},
        )

        assert event.kind == "tool_result"
        assert event.is_error is True
        assert "unknown tool" in str(event.content)

    @pytest.mark.asyncio
    async def test_invalid_json_marker_returns_error(self) -> None:
        service = ToolExecutionService({})

        event = await service.execute_tool_call(
            tool_use_id="toolu_1",
            tool_name="stub",
            tool_input={"_invalid_input_json": "{"},
        )

        assert event.kind == "tool_result"
        assert event.is_error is True
        assert "invalid_json" in str(event.content)

    @pytest.mark.asyncio
    async def test_tool_exception_returns_error(self) -> None:
        service = ToolExecutionService({"stub": _StubTool("stub", exc=ValueError("boom"))})

        event = await service.execute_tool_call(
            tool_use_id="toolu_1",
            tool_name="stub",
            tool_input={},
        )

        assert event.kind == "tool_result"
        assert event.is_error is True
        assert "boom" in str(event.content)

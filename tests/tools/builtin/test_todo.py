"""Tests for TodoTool."""

from __future__ import annotations

import pytest

from chariot.models.tool import ToolEntry
from chariot.tools.builtin.todo import TodoTool, _TodoStore


def _make_tool() -> TodoTool:
    return TodoTool.create(ToolEntry(name="todo", type="todo", enabled=True, options={}))


@pytest.mark.asyncio
async def test_todo_add_and_list() -> None:
    tool = _make_tool()
    store = _TodoStore()
    result = await tool.execute({"action": "add", "id": "task1", "text": "do something", "_todo_store": store})
    assert not result.get("is_error")
    assert "do something" in result["content"][0]["text"]

    result = await tool.execute({"action": "list", "_todo_store": store})
    assert "task1" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_todo_update_status() -> None:
    tool = _make_tool()
    store = _TodoStore()
    await tool.execute({"action": "add", "id": "t1", "text": "x", "_todo_store": store})
    result = await tool.execute({"action": "update", "id": "t1", "status": "completed", "_todo_store": store})
    assert not result.get("is_error")
    assert "✅" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_todo_remove() -> None:
    tool = _make_tool()
    store = _TodoStore()
    await tool.execute({"action": "add", "id": "t1", "text": "x", "_todo_store": store})
    result = await tool.execute({"action": "remove", "id": "t1", "_todo_store": store})
    assert not result.get("is_error")
    result = await tool.execute({"action": "list", "_todo_store": store})
    assert "当前无任务" in result["content"][0]["text"]

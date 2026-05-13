"""Tests for EditFileTool."""

from __future__ import annotations

from pathlib import Path

import pytest

from chariot.models.tool import ToolEntry
from chariot.tools.builtin.edit_file import EditFileTool


def _make_tool(**opts: object) -> EditFileTool:
    return EditFileTool.create(ToolEntry(name="edit_file", type="edit_file", enabled=True, options=opts))


@pytest.mark.asyncio
async def test_edit_file_basic(tmp_path: Path) -> None:
    tool = _make_tool()
    target = tmp_path / "test.py"
    target.write_text("hello world\nfoo bar\n")
    result = await tool.execute({"path": str(target), "old_string": "world", "new_string": "universe"})
    assert not result.get("is_error")
    assert target.read_text() == "hello universe\nfoo bar\n"


@pytest.mark.asyncio
async def test_edit_file_not_found(tmp_path: Path) -> None:
    tool = _make_tool()
    target = tmp_path / "nonexistent.py"
    result = await tool.execute({"path": str(target), "old_string": "x", "new_string": "y"})
    assert result.get("is_error")


@pytest.mark.asyncio
async def test_edit_file_old_string_not_found(tmp_path: Path) -> None:
    tool = _make_tool()
    target = tmp_path / "test.py"
    target.write_text("hello world")
    result = await tool.execute({"path": str(target), "old_string": "missing", "new_string": "y"})
    assert result.get("is_error")
    assert "未找到 old_string" in str(result["content"][0]["text"])

"""Tests for WriteFileTool."""

from __future__ import annotations

from pathlib import Path

import pytest

from chariot.models.tool import ToolEntry
from chariot.tools.builtin.write_file import WriteFileTool


def _make_tool(**opts: object) -> WriteFileTool:
    merged: dict[str, object] = {"allow_outside_cwd": True}
    merged.update(opts)
    return WriteFileTool.create(ToolEntry(name="write_file", type="write_file", enabled=True, options=merged))


@pytest.mark.asyncio
async def test_write_file_basic(tmp_path: Path) -> None:
    tool = _make_tool()
    target = tmp_path / "test.txt"
    result = await tool.execute({"path": str(target), "content": "hello world"})
    assert result["type"] == "tool_result"
    assert not result.get("is_error")
    assert target.read_text() == "hello world"


@pytest.mark.asyncio
async def test_write_file_append(tmp_path: Path) -> None:
    tool = _make_tool()
    target = tmp_path / "test.txt"
    target.write_text("existing")
    result = await tool.execute({"path": str(target), "content": " appended", "mode": "append"})
    assert not result.get("is_error")
    assert target.read_text() == "existing appended"


@pytest.mark.asyncio
async def test_write_file_creates_directories(tmp_path: Path) -> None:
    tool = _make_tool()
    target = tmp_path / "deep" / "nested" / "file.txt"
    result = await tool.execute({"path": str(target), "content": "deep"})
    assert not result.get("is_error")
    assert target.read_text() == "deep"


@pytest.mark.asyncio
async def test_write_file_outside_cwd_blocked(tmp_path: Path) -> None:
    tool = _make_tool(allow_outside_cwd=False)
    outside = tmp_path / "outside.txt"
    result = await tool.execute({"path": str(outside), "content": "x"})
    assert result.get("is_error")
    assert "禁止写入工作目录外" in str(result["content"][0]["text"])


@pytest.mark.asyncio
async def test_write_file_invalid_path() -> None:
    tool = _make_tool()
    result = await tool.execute({"path": "", "content": "x"})
    assert result.get("is_error")

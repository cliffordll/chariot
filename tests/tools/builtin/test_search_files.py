"""Tests for SearchFilesTool."""

from __future__ import annotations

from pathlib import Path

import pytest

from chariot.models.tool import ToolEntry
from chariot.tools.builtin.search_files import SearchFilesTool


def _make_tool(**opts: object) -> SearchFilesTool:
    return SearchFilesTool.create(ToolEntry(name="search_files", type="search_files", enabled=True, options=opts))


@pytest.mark.asyncio
async def test_search_files_basic(tmp_path: Path) -> None:
    tool = _make_tool()
    (tmp_path / "a.py").write_text("def hello(): pass\n")
    (tmp_path / "b.py").write_text("class Hello:\n    pass\n")
    result = await tool.execute({"pattern": "hello", "path": str(tmp_path)})
    assert not result.get("is_error")
    text = result["content"][0]["text"]
    assert "共 2 处匹配" in text or "共 1 处匹配" in text


@pytest.mark.asyncio
async def test_search_files_no_match(tmp_path: Path) -> None:
    tool = _make_tool()
    (tmp_path / "a.py").write_text("foo\n")
    result = await tool.execute({"pattern": "missing", "path": str(tmp_path)})
    assert not result.get("is_error")
    assert "未找到匹配" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_search_files_by_extension(tmp_path: Path) -> None:
    tool = _make_tool()
    (tmp_path / "a.py").write_text("target\n")
    (tmp_path / "b.txt").write_text("target\n")
    result = await tool.execute({"pattern": "target", "path": str(tmp_path), "file_extension": ".py"})
    text = result["content"][0]["text"]
    assert "a.py" in text
    assert "b.txt" not in text

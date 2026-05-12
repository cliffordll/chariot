"""Tests for WebSearchTool."""

from __future__ import annotations

import pytest

from chariot.agent.exceptions import ConfigError
from chariot.models.tool import ToolEntry
from chariot.tools.builtin.web_search import WebSearchTool


def _make_tool(**opts: object) -> WebSearchTool:
    return WebSearchTool.create(ToolEntry(name="web_search", type="web_search", enabled=True, options=opts))


@pytest.mark.asyncio
async def test_web_search_invalid_query() -> None:
    tool = _make_tool()
    result = await tool.execute({"query": ""})
    assert result.get("is_error")


@pytest.mark.asyncio
async def test_web_search_tavily_no_key() -> None:
    tool = _make_tool(backend="tavily")
    result = await tool.execute({"query": "python"})
    assert result.get("is_error")
    assert "TAVILY_API_KEY" in result["content"][0]["text"]


def test_web_search_invalid_backend() -> None:
    with pytest.raises(ConfigError):
        _make_tool(backend="invalid")

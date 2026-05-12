"""Tests for WebExtractTool."""

from __future__ import annotations

import pytest

from chariot.models.tool import ToolEntry
from chariot.tools.builtin.web_extract import WebExtractTool


def _make_tool(**opts: object) -> WebExtractTool:
    return WebExtractTool.create(ToolEntry(name="web_extract", type="web_extract", enabled=True, options=opts))


@pytest.mark.asyncio
async def test_web_extract_invalid_url() -> None:
    tool = _make_tool()
    result = await tool.execute({"url": ""})
    assert result.get("is_error")


@pytest.mark.asyncio
async def test_web_extract_domain_not_allowed() -> None:
    tool = _make_tool(allowed_domains=["example.com"])
    result = await tool.execute({"url": "https://other.com/page"})
    assert result.get("is_error")
    assert "不在 allowed_domains" in result["content"][0]["text"]

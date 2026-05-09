from __future__ import annotations

import pytest

from chariot.agent.config import ConfigError, ToolEntry
from chariot.tools.builtin.http_get import HttpGetTool


def _entry(options: dict) -> ToolEntry:
    return ToolEntry(name="http_get", type="http_get", enabled=True, options=options)


class TestHttpGetToolCreate:
    def test_accepts_canonical_allowed_domains_list(self) -> None:
        tool = HttpGetTool.create(
            _entry({"allowed_domains": ["example.com"], "max_bytes": 1024})
        )
        assert tool.allowed_domains == ("example.com",)

    def test_accepts_legacy_bracket_string(self) -> None:
        tool = HttpGetTool.create(
            _entry({"allowed_domains": "[example.com]", "max_bytes": 1024})
        )
        assert tool.allowed_domains == ("example.com",)

    def test_accepts_json_array_string(self) -> None:
        tool = HttpGetTool.create(
            _entry({"allowed_domains": '["example.com","openai.com"]', "max_bytes": 1024})
        )
        assert tool.allowed_domains == ("example.com", "openai.com")

    def test_rejects_non_string_items(self) -> None:
        with pytest.raises(ConfigError, match="allowed_domains"):
            HttpGetTool.create(_entry({"allowed_domains": [1], "max_bytes": 1024}))

"""CLI tool command tests."""

from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from chariot.cli.__main__ import app

runner = CliRunner()
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI_RE.sub("", text)


@pytest.fixture(autouse=True)
def _wide_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")


def test_tool_help_contains_list_show_probe_enable_disable_config() -> None:
    result = runner.invoke(app, ["tool", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    for sub in ("list", "show", "probe", "enable", "disable", "config"):
        assert sub in out


def test_tool_show_requires_name() -> None:
    result = runner.invoke(app, ["tool", "show"])
    assert result.exit_code != 0


def test_tool_probe_requires_name() -> None:
    result = runner.invoke(app, ["tool", "probe"])
    assert result.exit_code != 0

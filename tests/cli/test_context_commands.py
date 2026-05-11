"""CLI context command tests."""

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


def test_root_help_contains_context() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "context" in _plain(result.output)


def test_context_help_contains_list_traces_inspect() -> None:
    result = runner.invoke(app, ["context", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    for sub in ("list", "traces", "inspect"):
        assert sub in out

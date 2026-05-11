"""CLI trace command tests(B1 phase 4)。

只覆盖 help / 参数校验,不动 DB(避免依赖 runtime)。
"""

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


def test_trace_help_lists_subcommands() -> None:
    result = runner.invoke(app, ["trace", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    for sub in ("list", "show", "view", "reconcile"):
        assert sub in out


def test_trace_list_help_lists_filters() -> None:
    result = runner.invoke(app, ["trace", "list", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    for flag in ("--conversation", "--task", "--provider", "--status", "--limit"):
        assert flag in out


def test_trace_show_requires_turn_id() -> None:
    result = runner.invoke(app, ["trace", "show"])
    assert result.exit_code != 0


def test_trace_view_requires_turn_id() -> None:
    result = runner.invoke(app, ["trace", "view"])
    assert result.exit_code != 0


def test_trace_reconcile_help() -> None:
    result = runner.invoke(app, ["trace", "reconcile", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    assert "--older-than" in out

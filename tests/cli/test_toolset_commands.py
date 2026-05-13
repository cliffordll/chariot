"""CLI toolset command tests."""

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


def test_toolset_help_lists_subcommands() -> None:
    result = runner.invoke(app, ["toolset", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    for sub in ("list", "show", "add", "update", "delete", "members"):
        assert sub in out


def test_toolset_members_help() -> None:
    result = runner.invoke(app, ["toolset", "members", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    for sub in ("add", "delete"):
        assert sub in out


def test_toolset_show_requires_name() -> None:
    result = runner.invoke(app, ["toolset", "show"])
    assert result.exit_code != 0


def test_toolset_members_add_requires_two_args() -> None:
    # 缺 tool_name
    result = runner.invoke(app, ["toolset", "members", "add", "ts"])
    assert result.exit_code != 0


def test_toolset_remove_requires_name() -> None:
    result = runner.invoke(app, ["toolset", "remove"])
    assert result.exit_code != 0

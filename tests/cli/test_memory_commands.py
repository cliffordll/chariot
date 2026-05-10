from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from chariot.cli.__main__ import app

runner = CliRunner()
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI_RE.sub("", text)


def test_memory_help_has_expected_commands() -> None:
    result = runner.invoke(app, ["memory", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    for sub in ("list", "show", "add", "update", "delete", "pin", "unpin", "archive", "restore", "events", "links", "search"):
        assert sub in out


@pytest.mark.parametrize("sub", ["memory"])
def test_memory_group_has_list(sub: str) -> None:
    result = runner.invoke(app, [sub, "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    assert "list" in out

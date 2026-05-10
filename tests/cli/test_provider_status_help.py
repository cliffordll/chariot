"""Provider CLI A5 help coverage."""

from __future__ import annotations

import re

from typer.testing import CliRunner

from chariot.cli.__main__ import app

runner = CliRunner()
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI_RE.sub("", text)


def test_provider_help_shows_status() -> None:
    result = runner.invoke(app, ["provider", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    assert "status" in out

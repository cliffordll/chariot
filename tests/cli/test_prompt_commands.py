from __future__ import annotations

from typer.testing import CliRunner

from chariot.cli.__main__ import app


runner = CliRunner()


def test_root_help_includes_prompt_group() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "prompt" in result.output


def test_prompt_help_includes_management_commands() -> None:
    result = runner.invoke(app, ["prompt", "--help"])
    assert result.exit_code == 0
    for sub in ("list", "show", "add", "update", "activate", "versions", "version", "traces", "inspect"):
        assert sub in result.output
    for section in ("查看", "管理", "追踪"):
        assert section in result.output
    assert "\ufffd" not in result.output

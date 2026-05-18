from __future__ import annotations

import re
from pathlib import Path

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


def test_root_help_lists_main_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    for sub in (
        "status",
        "logs",
        "stats",
        "chat",
        "provider",
        "tool",
        "agent",
        "task",
        "job",
        "conversation",
        "context",
        "memory",
        "eval",
        "skill",
        "checkpoint",
        "prompt",
    ):
        assert sub in out


@pytest.mark.parametrize(
    "sub",
    [
        "status",
        "logs",
        "stats",
        "chat",
        "provider",
        "tool",
        "agent",
        "task",
        "job",
        "conversation",
        "context",
        "memory",
        "eval",
        "skill",
        "checkpoint",
        "prompt",
    ],
)
@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_subcommand_help(sub: str, flag: str) -> None:
    result = runner.invoke(app, [sub, flag])
    assert result.exit_code == 0


def test_tool_help_contains_expected_subcommands() -> None:
    result = runner.invoke(app, ["tool", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    for sub in ("list", "show", "probe", "enable", "disable", "config"):
        assert sub in out


@pytest.mark.parametrize("sub", ["context", "memory", "eval", "skill", "checkpoint", "prompt"])
def test_phase4_subcommand_groups_have_list(sub: str) -> None:
    result = runner.invoke(app, [sub, "--help"])
    assert result.exit_code == 0
    assert "list" in _plain(result.output)


def test_provider_help_contains_crud_and_probe() -> None:
    result = runner.invoke(app, ["provider", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    for sub in ("list", "show", "use", "probe", "add", "update", "delete", "copy", "status"):
        assert sub in out


def test_conversation_help_contains_list_show_delete_rename() -> None:
    result = runner.invoke(app, ["conversation", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    for sub in ("list", "show", "delete", "rename"):
        assert sub in out


def test_chat_help_contains_conversation_provider_and_agent_options() -> None:
    result = runner.invoke(app, ["chat", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    for flag in ("--conversation", "--provider", "--agent"):
        assert flag in out
    assert "new|ULID" in out


def test_provider_probe_help_contains_override_options() -> None:
    result = runner.invoke(app, ["provider", "probe", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    for flag in ("--model", "--base-url", "--api-key"):
        assert flag in out


def test_chat_invalid_conversation_value_fails_locally() -> None:
    result = runner.invoke(app, ["chat", "--conversation", "foo", "hi"])
    assert result.exit_code != 0
    out = _plain(result.output)
    assert "new" in out and "ULID" in out


def test_tool_enable_requires_name() -> None:
    result = runner.invoke(app, ["tool", "enable"])
    assert result.exit_code != 0


def test_conversation_show_requires_id() -> None:
    result = runner.invoke(app, ["conversation", "show"])
    assert result.exit_code != 0


def test_provider_use_requires_name() -> None:
    result = runner.invoke(app, ["provider", "use"])
    assert result.exit_code != 0


def test_provider_add_requires_name_and_type() -> None:
    result = runner.invoke(app, ["provider", "add"])
    assert result.exit_code != 0


def test_removed_or_renamed_commands_fail() -> None:
    for argv in (["model", "list"], ["start"], ["stop"], ["config", "--help"], ["upstream"]):
        result = runner.invoke(app, argv)
        assert result.exit_code != 0


def test_conversation_list_runs_with_tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chariot.cli import _runtime

    monkeypatch.setattr(_runtime, "DEFAULT_DB_PATH", tmp_path / "chariot.db")
    result = runner.invoke(app, ["conversation", "list"])
    assert result.exit_code == 0


def test_provider_list_shows_id_column(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chariot.cli import _runtime

    monkeypatch.setattr(_runtime, "DEFAULT_DB_PATH", tmp_path / "chariot.db")
    add_result = runner.invoke(app, ["provider", "add", "--name", "Mock CLI", "--type", "mock"])
    assert add_result.exit_code == 0

    result = runner.invoke(app, ["provider", "list"])
    assert result.exit_code == 0
    out = _plain(result.output)
    assert "id" in out
    assert "Mock CLI" in out


def test_prompt_list_shows_id_column(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chariot.cli import _runtime

    monkeypatch.setattr(_runtime, "DEFAULT_DB_PATH", tmp_path / "chariot.db")
    add_result = runner.invoke(app, ["prompt", "add", "review"])
    assert add_result.exit_code == 0

    result = runner.invoke(app, ["prompt", "list"])
    assert result.exit_code == 0
    out = _plain(result.output)
    assert "id" in out
    assert "review" in out


def test_toolset_list_shows_id_column(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chariot.cli import _runtime

    monkeypatch.setattr(_runtime, "DEFAULT_DB_PATH", tmp_path / "chariot.db")
    add_result = runner.invoke(app, ["toolset", "add", "--name", "ops"])
    assert add_result.exit_code == 0

    result = runner.invoke(app, ["toolset", "list"])
    assert result.exit_code == 0
    out = _plain(result.output)
    assert "id" in out
    assert "ops" in out


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_root_help_accepts_short_and_long(flag: str) -> None:
    result = runner.invoke(app, [flag])
    assert result.exit_code == 0
    assert "chariot" in _plain(result.output)


def test_unknown_subcommand_fails() -> None:
    result = runner.invoke(app, ["ghost-cmd"])
    assert result.exit_code != 0


def test_chat_protocol_option_removed() -> None:
    result = runner.invoke(app, ["chat", "--protocol", "messages", "hi"])
    assert result.exit_code != 0


def test_chat_invalid_max_tokens_fails() -> None:
    result = runner.invoke(app, ["chat", "--max-tokens", "abc", "hi"])
    assert result.exit_code != 0

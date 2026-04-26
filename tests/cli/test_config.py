"""`chariot config init / show` 行为测试 —— 走 tmp 路径,不污染用户家目录。"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from chariot.cli.__main__ import app
from chariot.server.config import ConfigLoader

runner = CliRunner()


@pytest.fixture(autouse=True)
def _wide_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """COLUMNS=200 防 rich 把长 Windows 路径换行拆成多段,破坏 substring 断言。"""
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")


@pytest.fixture
def tmp_default_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """把 ConfigLoader.DEFAULT_PATH 重定向到 tmp 目录,避免动 ~/.chariot/。"""
    target = tmp_path / "config.toml"
    monkeypatch.setattr(ConfigLoader, "DEFAULT_PATH", target)
    return target


# ---------- init ----------


def test_init_writes_template_when_missing(tmp_default_path: Path) -> None:
    assert not tmp_default_path.exists()
    result = runner.invoke(app, ["config", "init"])
    assert result.exit_code == 0
    assert tmp_default_path.exists()
    text = tmp_default_path.read_text(encoding="utf-8")
    # 模板里至少有这些关键字段
    assert "[[models]]" in text
    assert 'type = "mock"' in text
    assert 'type = "anthropic"' in text
    assert "[active]" in text


def test_init_refuses_overwrite_without_force(tmp_default_path: Path) -> None:
    tmp_default_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_default_path.write_text("# original user content\n", encoding="utf-8")

    result = runner.invoke(app, ["config", "init"])
    assert result.exit_code != 0
    # 用户文件没被覆盖
    assert tmp_default_path.read_text(encoding="utf-8") == "# original user content\n"


def test_init_with_force_overwrites(tmp_default_path: Path) -> None:
    tmp_default_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_default_path.write_text("# original\n", encoding="utf-8")

    result = runner.invoke(app, ["config", "init", "--force"])
    assert result.exit_code == 0
    # 模板覆盖了
    text = tmp_default_path.read_text(encoding="utf-8")
    assert "# original" not in text
    assert "[[models]]" in text


# ---------- show ----------


def test_show_says_missing_when_no_file(tmp_default_path: Path) -> None:
    assert not tmp_default_path.exists()
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    out = result.output
    assert str(tmp_default_path) in out
    assert "不存在" in out


def test_show_prints_file_when_present(tmp_default_path: Path) -> None:
    tmp_default_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_default_path.write_text(
        '[[models]]\nname = "abc"\ntype = "mock"\n',
        encoding="utf-8",
    )
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert 'name = "abc"' in result.output


def test_show_reports_env_override(
    tmp_default_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_cfg = tmp_path / "via-env.toml"
    env_cfg.write_text(
        '[[models]]\nname = "from_env"\ntype = "mock"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CHARIOT_CONFIG", str(env_cfg))

    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    out = result.output
    assert str(env_cfg) in out
    assert "覆盖" in out
    assert 'name = "from_env"' in out

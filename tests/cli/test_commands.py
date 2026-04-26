"""CLI 子命令结构测试(阶段 4.2 · 不调 server,只验 typer 接线)。

用 typer.testing.CliRunner 执行 `chariot` / `chariot <cmd> --help`,断言:
- 根命令和所有子命令可用(`chariot --help` 退出 0)
- 每个子命令 `--help` 可显示(证明 register 正确)
- 无效子命令的退出码非 0(typer 默认行为)
- 必填参数缺失时子命令退出码非 0(以 `provider add` 为例)
"""

from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from chariot.cli.__main__ import app

runner = CliRunner()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    """剥 ANSI 颜色 / 样式转义,方便 substring 断言跨平台稳定。"""
    return _ANSI_RE.sub("", text)


@pytest.fixture(autouse=True)
def _wide_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """两件事:
    1. COLUMNS=200 —— CI 终端比本地窄,rich 会把 `--quiet` 等 option 换行拆开
    2. NO_COLOR=1 + TERM=dumb —— 禁 rich 在 help 里插 ANSI 颜色码,否则
       `\\x1b[1m--quiet` 里虽然含 --quiet,但 rich 可能把 `--` 和 `quiet`
       分别上色导致中间插入转义 → substring 断言失败
    """
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
    # Windows CI runner 默认 stdout 用 cp1252,中文 option help 编码失败崩
    # 测试;Linux 默认 utf-8 不受影响。统一强制 utf-8。
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")


def test_root_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    # 关键子命令名都出现
    out = _plain(result.output)
    for sub in ("status", "start", "stop", "logs", "stats", "chat"):
        assert sub in out, f"--help 输出里缺少子命令 {sub!r}"


@pytest.mark.parametrize(
    "sub",
    ["status", "start", "stop", "logs", "stats", "chat"],
)
@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_subcommand_help(sub: str, flag: str) -> None:
    result = runner.invoke(app, [sub, flag])
    assert result.exit_code == 0, f"{sub} {flag} 应成功,实际 exit={result.exit_code}"


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_root_help_accepts_short_and_long(flag: str) -> None:
    result = runner.invoke(app, [flag])
    assert result.exit_code == 0
    assert "chariot" in _plain(result.output)


def test_unknown_subcommand_fails() -> None:
    result = runner.invoke(app, ["ghost-cmd"])
    assert result.exit_code != 0


def test_upstream_subcommand_removed() -> None:
    """v0 架构没有 upstream 概念,`chariot upstream` 子命令应不存在。"""
    result = runner.invoke(app, ["upstream"])
    assert result.exit_code != 0


def test_chat_protocol_option_removed() -> None:
    """0.2.0 起单协议化,`chat --protocol ...` 选项已下线。"""
    result = runner.invoke(app, ["chat", "--protocol", "messages", "hi"])
    assert result.exit_code != 0


def test_chat_invalid_max_tokens_fails() -> None:
    """`--max-tokens` 必须是 int;非数字 typer 自带 parser 阶段就报错(不触 server)。"""
    result = runner.invoke(app, ["chat", "--max-tokens", "abc", "hi"])
    assert result.exit_code != 0


# ---------- --quiet 全局 flag ----------


def test_quiet_flag_accepted_by_root_help() -> None:
    """根 --help 里有 --quiet / -q 选项。"""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    assert "--quiet" in out
    assert "-q" in out


def test_quiet_flag_sets_renderer_state() -> None:
    """--quiet 触发根 callback 后,Renderer.QUIET = True。"""
    from chariot.cli.core.render import Renderer

    Renderer.QUIET = False  # 保险丝
    # 用一个必然失败的子命令快速走完 callback + 子命令参数校验(不触 server)
    runner.invoke(app, ["--quiet", "chat", "--max-tokens", "abc", "hi"])
    assert Renderer.QUIET is True
    Renderer.QUIET = False  # 复位,避免污染后续 test


def test_short_quiet_flag() -> None:
    from chariot.cli.core.render import Renderer

    Renderer.QUIET = False
    runner.invoke(app, ["-q", "chat", "--max-tokens", "abc", "hi"])
    assert Renderer.QUIET is True
    Renderer.QUIET = False

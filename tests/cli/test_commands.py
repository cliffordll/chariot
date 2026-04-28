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
    # 关键子命令名都出现(0.4.0 加 tool / conversation)
    out = _plain(result.output)
    for sub in (
        "status",
        "start",
        "stop",
        "logs",
        "stats",
        "chat",
        "model",
        "tool",
        "conversation",
    ):
        assert sub in out, f"--help 输出里缺少子命令 {sub!r}"


@pytest.mark.parametrize(
    "sub",
    [
        "status",
        "start",
        "stop",
        "logs",
        "stats",
        "chat",
        "model",
        "tool",
        "conversation",
    ],
)
@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_subcommand_help(sub: str, flag: str) -> None:
    result = runner.invoke(app, [sub, flag])
    assert result.exit_code == 0, f"{sub} {flag} 应成功,实际 exit={result.exit_code}"


def test_tool_subcommand_group_has_list_enable_disable_config() -> None:
    """0.4.0 chariot tool 子命令组。"""
    result = runner.invoke(app, ["tool", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    for sub in ("list", "enable", "disable", "config"):
        assert sub in out, f"`chariot tool --help` 缺少子命令 {sub!r}"


def test_conversation_subcommand_group_has_list_show_rm_rename() -> None:
    """0.4.0 chariot conversation 子命令组。"""
    result = runner.invoke(app, ["conversation", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    for sub in ("list", "show", "rm", "rename"):
        assert sub in out, f"`chariot conversation --help` 缺少子命令 {sub!r}"


def test_chat_has_conversation_option() -> None:
    """0.4.0 chariot chat 加 --conversation 选项。"""
    result = runner.invoke(app, ["chat", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    assert "--conversation" in out
    # metavar 让用户立刻看到取值范围,不必读 help 长文(0.4.0 后加)
    assert "new|ULID" in out


def test_chat_conversation_invalid_value_dies_locally() -> None:
    """非法 --conversation 值 → CLI 立刻 die,不打到 server。

    早期版本会原样透传到 server,server 返 400 + 中文错误,但 CLI 端把它抬成
    Renderer.error_bubble 后再 typer.Exit;非法值的反馈链路过长,且空字符串会
    silent fall through 到 stateless,体验差。改为本地正则校验。
    """
    result = runner.invoke(app, ["chat", "--conversation", "foo", "hi"])
    assert result.exit_code != 0
    out = _plain(result.output)
    # 错误文案应包含合法取值提示("new" 或 ULID)
    assert "new" in out and "ULID" in out


def test_tool_enable_requires_name() -> None:
    """`chariot tool enable` 没传 name → 退出码非 0。"""
    result = runner.invoke(app, ["tool", "enable"])
    assert result.exit_code != 0


def test_conversation_show_requires_id() -> None:
    """`chariot conversation show` 没传 id → 退出码非 0。"""
    result = runner.invoke(app, ["conversation", "show"])
    assert result.exit_code != 0


def test_model_subcommand_group_has_list_and_crud() -> None:
    """0.3.1 起 model 子命令组 = list / probe / add / edit / rm / duplicate(use 已删)。"""
    result = runner.invoke(app, ["model", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    for sub in ("list", "probe", "add", "edit", "rm", "duplicate"):
        assert sub in out, f"`chariot model --help` 缺少子命令 {sub!r}"


def test_model_use_subcommand_removed() -> None:
    """0.3.1 路由模型重构:active 概念删除,`chariot model use` 也下线。"""
    result = runner.invoke(app, ["model", "use", "anything"])
    assert result.exit_code != 0


def test_model_add_requires_name_and_type() -> None:
    """`chariot model add` 没传 --name / --type 时 typer 报参数缺失。"""
    result = runner.invoke(app, ["model", "add"])
    assert result.exit_code != 0


def test_config_subcommand_removed() -> None:
    """0.3.0 起 chariot config init/show 子命令组废弃(模型配置改 DB-backed)。"""
    result = runner.invoke(app, ["config", "--help"])
    assert result.exit_code != 0


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

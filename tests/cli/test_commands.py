"""CLI 子命令结构测试(0.6.0 库化版 · 不调 server,只验 typer 接线)。

用 typer.testing.CliRunner 执行 `chariot` / `chariot <cmd> --help`,断言:
- 根命令和所有子命令可用(`chariot --help` 退出 0)
- 每个子命令 `--help` 可显示(证明 register 正确)
- 无效子命令的退出码非 0(typer 默认行为)
- 必填参数缺失时子命令退出码非 0(以 `model add` 为例)
- 库化后撤掉的 daemon 命令(`start` / `stop`)走子命令时退出码非 0
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
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")


def test_root_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    # 0.6.0:撤 start / stop;保留剩余 7 个子命令
    for sub in (
        "status",
        "logs",
        "stats",
        "chat",
        "provider",
        "tool",
        "convo",
        "memory",
        "eval",
        "skill",
        "checkpoint",
    ):
        assert sub in out, f"--help 输出里缺少子命令 {sub!r}"


@pytest.mark.parametrize(
    "sub",
    [
        "status",
        "logs",
        "stats",
        "chat",
        "provider",
        "tool",
        "convo",
        "memory",
        "eval",
        "skill",
        "checkpoint",
    ],
)
@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_subcommand_help(sub: str, flag: str) -> None:
    result = runner.invoke(app, [sub, flag])
    assert result.exit_code == 0, f"{sub} {flag} 应成功,实际 exit={result.exit_code}"


def test_tool_subcommand_group_has_list_enable_disable_config() -> None:
    """`chariot tool` 子命令组(0.4.0)。"""
    result = runner.invoke(app, ["tool", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    for sub in ("list", "enable", "disable", "config"):
        assert sub in out, f"`chariot tool --help` 缺少子命令 {sub!r}"


@pytest.mark.parametrize("sub", ["memory", "eval", "skill", "checkpoint"])
def test_phase4_subcommand_groups_have_list(sub: str) -> None:
    result = runner.invoke(app, [sub, "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    assert "list" in out, f"`chariot {sub} --help` 缺少 list 子命令"


def test_convo_subcommand_group_has_list_show_rm_rename() -> None:
    """`chariot convo` 子命令组(0.4.0;0.6.0 起 conversation → convo)。"""
    result = runner.invoke(app, ["convo", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    for sub in ("list", "show", "rm", "rename"):
        assert sub in out, f"`chariot convo --help` 缺少子命令 {sub!r}"


def test_chat_has_convo_option() -> None:
    """`chariot chat` 加 --convo 选项(0.4.0;0.6.0 起 conversation → convo)。"""
    result = runner.invoke(app, ["chat", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    assert "--convo" in out
    # metavar 让用户立刻看到取值范围,不必读 help 长文
    assert "new|ULID" in out


def test_chat_has_provider_option() -> None:
    """`chariot chat` 加 --provider 选项(v7 起;不传走 DB 默认)。"""
    result = runner.invoke(app, ["chat", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    assert "--provider" in out


def test_chat_has_override_options() -> None:
    """S.7.3 起:`chariot chat` 加 --model / --base-url / --api-key 三个 per-call 覆盖。"""
    result = runner.invoke(app, ["chat", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    for flag in ("--model", "--base-url", "--api-key"):
        assert flag in out, f"`chariot chat --help` 缺少 {flag}"


def test_provider_probe_has_override_options() -> None:
    """S.7.3 起:`chariot provider probe` 也接 --model / --base-url / --api-key。"""
    result = runner.invoke(app, ["provider", "probe", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    for flag in ("--model", "--base-url", "--api-key"):
        assert flag in out, f"`chariot provider probe --help` 缺少 {flag}"


def test_chat_convo_invalid_value_dies_locally() -> None:
    """非法 --convo 值 → CLI 立刻 die,不打 AIAgent。"""
    result = runner.invoke(app, ["chat", "--convo", "foo", "hi"])
    assert result.exit_code != 0
    out = _plain(result.output)
    # 错误文案应包含合法取值提示("new" 或 ULID)
    assert "new" in out and "ULID" in out


def test_tool_enable_requires_name() -> None:
    """`chariot tool enable` 没传 name → 退出码非 0。"""
    result = runner.invoke(app, ["tool", "enable"])
    assert result.exit_code != 0


def test_convo_show_requires_id() -> None:
    """`chariot convo show` 没传 id → 退出码非 0。"""
    result = runner.invoke(app, ["convo", "show"])
    assert result.exit_code != 0


def test_provider_subcommand_group_has_list_show_use_and_crud() -> None:
    """`chariot provider` 子命令(0.6.0 起):list / show / use / probe / add / edit / rm / copy。

    v7 起新增 `show`(展示 entry,默认显示当前默认)+ `use`(设默认)。
    """
    result = runner.invoke(app, ["provider", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    for sub in ("list", "show", "use", "probe", "add", "update", "delete", "rm", "copy"):
        assert sub in out, f"`chariot provider --help` 缺少子命令 {sub!r}"


def test_provider_use_requires_name() -> None:
    """`chariot provider use` 不带参数 → typer 报参数缺失。"""
    result = runner.invoke(app, ["provider", "use"])
    assert result.exit_code != 0


def test_provider_add_requires_name_and_type() -> None:
    """`chariot provider add` 没传 --name / --type 时 typer 报参数缺失。"""
    result = runner.invoke(app, ["provider", "add"])
    assert result.exit_code != 0


def test_model_subcommand_renamed() -> None:
    """0.6.0 起 `chariot model` rename 成 `chariot provider`,旧名退出码非 0。"""
    result = runner.invoke(app, ["model", "list"])
    assert result.exit_code != 0


def test_start_subcommand_removed() -> None:
    """0.6.0 库化后撤 daemon `start` 命令。"""
    result = runner.invoke(app, ["start"])
    assert result.exit_code != 0


def test_stop_subcommand_removed() -> None:
    """0.6.0 库化后撤 daemon `stop` 命令。"""
    result = runner.invoke(app, ["stop"])
    assert result.exit_code != 0


def test_config_subcommand_removed() -> None:
    """0.3.0 起 chariot config init/show 子命令组废弃(模型配置改 DB-backed)。"""
    result = runner.invoke(app, ["config", "--help"])
    assert result.exit_code != 0


def test_conversation_subcommand_renamed() -> None:
    """0.6.0 起 `chariot conversation` rename 成 `chariot convo`,旧名退出码非 0。"""
    result = runner.invoke(app, ["conversation", "list"])
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
    """`--max-tokens` 必须是 int;非数字 typer 自带 parser 阶段就报错。"""
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
    from chariot.cli.render import Renderer

    Renderer.QUIET = False  # 保险丝
    # 用一个必然失败的子命令快速走完 callback + 子命令参数校验
    runner.invoke(app, ["--quiet", "chat", "--max-tokens", "abc", "hi"])
    assert Renderer.QUIET is True
    Renderer.QUIET = False  # 复位,避免污染后续 test


def test_short_quiet_flag() -> None:
    from chariot.cli.render import Renderer

    Renderer.QUIET = False
    runner.invoke(app, ["-q", "chat", "--max-tokens", "abc", "hi"])
    assert Renderer.QUIET is True
    Renderer.QUIET = False


def test_convo_delete_subcommand_visible() -> None:
    result = runner.invoke(app, ["convo", "--help"])
    assert result.exit_code == 0
    assert "delete" in _plain(result.output)


def test_provider_delete_subcommand_visible() -> None:
    result = runner.invoke(app, ["provider", "--help"])
    assert result.exit_code == 0
    assert "delete" in _plain(result.output)

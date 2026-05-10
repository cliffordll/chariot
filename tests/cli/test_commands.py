"""CLI 鐎涙劕鎳℃禒銈囩波閺嬪嫭绁寸拠?0.6.0 鎼存挸瀵查悧?璺?娑撳秷鐨?server,閸欘亪鐛?typer 閹恒儳鍤?閵?

閻?typer.testing.CliRunner 閹笛嗩攽 `chariot` / `chariot <cmd> --help`,閺傤叀鈻?
- 閺嶇懓鎳℃禒銈呮嫲閹碘偓閺堝鐡欓崨鎴掓姢閸欘垳鏁?`chariot --help` 闁偓閸?0)
- 濮ｅ繋閲滅€涙劕鎳℃禒?`--help` 閸欘垱妯夌粈?鐠囦焦妲?register 濮濓絿鈥?
- 閺冪姵鏅ョ€涙劕鎳℃禒銈囨畱闁偓閸戣櫣鐖滈棃?0(typer 姒涙顓荤悰灞艰礋)
- 韫囧懎锝為崣鍌涙殶缂傚搫銇戦弮璺虹摍閸涙垝鎶ら柅鈧崙铏圭垳闂?0(娴?`model add` 娑撹桨绶?
- 鎼存挸瀵查崥搴㈡寵閹哄娈?daemon 閸涙垝鎶?`start` / `stop`)鐠ф澘鐡欓崨鎴掓姢閺冨爼鈧偓閸戣櫣鐖滈棃?0
"""

from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from chariot.cli.__main__ import app

runner = CliRunner()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    """閸?ANSI 妫版粏澹?/ 閺嶅嘲绱℃潪顑跨疅,閺傞€涚┒ substring 閺傤叀鈻堢捄銊ラ挬閸欐壆菙鐎规哎鈧?""
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
    # 0.6.0:鎾?start / stop;淇濈暀鍓╀綑 7 涓瓙鍛戒护
    for sub in (
        "status",
        "logs",
        "stats",
        "chat",
        "provider",
        "tool",
        "conversation",
        "memory",
        "eval",
        "skill",
        "checkpoint",
        "prompt",
    ):
        assert sub in out, f"--help 鏉堟挸鍤柌宀€宸辩亸鎴濈摍閸涙垝鎶?{sub!r}"


@pytest.mark.parametrize(
    "sub",
    [
        "status",
        "logs",
        "stats",
        "chat",
        "provider",
        "tool",
        "conversation",
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
    assert result.exit_code == 0, f"{sub} {flag} 鎼存梹鍨氶崝?鐎圭偤妾?exit={result.exit_code}"


def test_tool_subcommand_group_has_list_enable_disable_config() -> None:
    """`chariot tool` 鐎涙劕鎳℃禒銈囩矋(0.4.0)閵?"""
    result = runner.invoke(app, ["tool", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    for sub in ("list", "enable", "disable", "config"):
        assert sub in out, f"`chariot tool --help` 缂傚搫鐨€涙劕鎳℃禒?{sub!r}"


@pytest.mark.parametrize("sub", ["memory", "eval", "skill", "checkpoint", "prompt"])
def test_phase4_subcommand_groups_have_list(sub: str) -> None:
    result = runner.invoke(app, [sub, "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    assert "list" in out, f"`chariot {sub} --help` 缂傚搫鐨?list 鐎涙劕鎳℃禒?"


def test_prompt_subcommand_group_has_list_show_add_update_activate_version_traces_inspect() -> None:
    result = runner.invoke(app, ["prompt", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    for sub in ("list", "show", "add", "update", "activate", "versions", "version", "traces", "inspect"):
        assert sub in out, f"`chariot prompt --help` 缂傚搫鐨€涙劕鎳℃禒?{sub!r}"

def test_convo_subcommand_group_has_list_show_rm_rename() -> None:
    """`chariot conversation` 鐎涙劕鎳℃禒銈囩矋(0.4.0;0.6.0 鐠?conversation 閳?conversation)閵?""
    result = runner.invoke(app, ["conversation", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    for sub in ("list", "show", "rm", "rename"):
        assert sub in out, f"`chariot conversation --help` 缂傚搫鐨€涙劕鎳℃禒?{sub!r}"


def test_chat_has_convo_option() -> None:
    """`chariot chat` 閸?--conversation 闁銆?0.4.0;0.6.0 鐠?conversation 閳?conversation)閵?""
    result = runner.invoke(app, ["chat", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    assert "--conversation" in out
    # metavar 鐠佲晝鏁ら幋椋庣彌閸掕崵婀呴崚鏉垮絿閸婅壈瀵栭崶?娑撳秴绻€鐠?help 闂€鎸庢瀮
    assert "new|ULID" in out


def test_chat_has_provider_option() -> None:
    """`chariot chat` 閸?--provider 闁銆?v7 鐠?娑撳秳绱剁挧?DB 姒涙顓?閵?""
    result = runner.invoke(app, ["chat", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    assert "--provider" in out


def test_chat_has_override_options() -> None:
    """S.7.3 鐠?`chariot chat` 閸?--model / --base-url / --api-key 娑撳閲?per-call 鐟曞棛娲婇妴?""
    result = runner.invoke(app, ["chat", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    for flag in ("--model", "--base-url", "--api-key"):
        assert flag in out, f"`chariot chat --help` 缂傚搫鐨?{flag}"


def test_provider_probe_has_override_options() -> None:
    """S.7.3 鐠?`chariot provider probe` 娑旂喐甯?--model / --base-url / --api-key閵?""
    result = runner.invoke(app, ["provider", "probe", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    for flag in ("--model", "--base-url", "--api-key"):
        assert flag in out, f"`chariot provider probe --help` 缂傚搫鐨?{flag}"


def test_chat_convo_invalid_value_dies_locally() -> None:
    """闂堢偞纭?--conversation 閸?閳?CLI 缁斿鍩?die,娑撳秵澧?AIAgent閵?""
    result = runner.invoke(app, ["chat", "--conversation", "foo", "hi"])
    assert result.exit_code != 0
    out = _plain(result.output)
    # 闁挎瑨顕ら弬鍥攳鎼存柨瀵橀崥顐㈡値濞夋洖褰囬崐鍏煎絹缁€?"new" 閹?ULID)
    assert "new" in out and "ULID" in out


def test_tool_enable_requires_name() -> None:
    """`chariot tool enable` 濞屸€茬炊 name 閳?闁偓閸戣櫣鐖滈棃?0閵?""
    result = runner.invoke(app, ["tool", "enable"])
    assert result.exit_code != 0


def test_convo_show_requires_id() -> None:
    """`chariot conversation show` 濞屸€茬炊 id 閳?闁偓閸戣櫣鐖滈棃?0閵?""
    result = runner.invoke(app, ["conversation", "show"])
    assert result.exit_code != 0


def test_provider_subcommand_group_has_list_show_use_and_crud() -> None:
    """`chariot provider` 鐎涙劕鎳℃禒?0.6.0 鐠?:list / show / use / probe / add / edit / rm / copy閵?

    v7 鐠ч攱鏌婃晶?`show`(鐏炴洜銇?entry,姒涙顓婚弰鍓с仛瑜版挸澧犳妯款吇)+ `use`(鐠侀箖绮拋?閵?
    """
    result = runner.invoke(app, ["provider", "--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    for sub in ("list", "show", "use", "probe", "add", "update", "delete", "rm", "copy"):
        assert sub in out, f"`chariot provider --help` 缂傚搫鐨€涙劕鎳℃禒?{sub!r}"


def test_provider_use_requires_name() -> None:
    """`chariot provider use` 娑撳秴鐢崣鍌涙殶 閳?typer 閹躲儱寮弫鎵繁婢朵究鈧?""
    result = runner.invoke(app, ["provider", "use"])
    assert result.exit_code != 0


def test_provider_add_requires_name_and_type() -> None:
    """`chariot provider add` 濞屸€茬炊 --name / --type 閺?typer 閹躲儱寮弫鎵繁婢朵究鈧?""
    result = runner.invoke(app, ["provider", "add"])
    assert result.exit_code != 0


def test_model_subcommand_renamed() -> None:
    """0.6.0 鐠?`chariot model` rename 閹?`chariot provider`,閺冄冩倳闁偓閸戣櫣鐖滈棃?0閵?""
    result = runner.invoke(app, ["model", "list"])
    assert result.exit_code != 0


def test_start_subcommand_removed() -> None:
    """0.6.0 鎼存挸瀵查崥搴㈡寵 daemon `start` 閸涙垝鎶ら妴?""
    result = runner.invoke(app, ["start"])
    assert result.exit_code != 0


def test_stop_subcommand_removed() -> None:
    """0.6.0 鎼存挸瀵查崥搴㈡寵 daemon `stop` 閸涙垝鎶ら妴?""
    result = runner.invoke(app, ["stop"])
    assert result.exit_code != 0


def test_config_subcommand_removed() -> None:
    """0.3.0 鐠?chariot config init/show 鐎涙劕鎳℃禒銈囩矋鎼寸喎绱?濡€崇€烽柊宥囩枂閺€?DB-backed)閵?""
    result = runner.invoke(app, ["config", "--help"])
    assert result.exit_code != 0


def test_conversation_subcommand_renamed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`conversation` 閻滄澘婀弰顖欏瘜閸氬秲鈧?""
    from chariot.cli import _runtime

    monkeypatch.setattr(_runtime, "DEFAULT_DB_PATH", tmp_path / "chariot.db")
    result = runner.invoke(app, ["conversation", "list"])
    assert result.exit_code == 0


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_root_help_accepts_short_and_long(flag: str) -> None:
    result = runner.invoke(app, [flag])
    assert result.exit_code == 0
    assert "chariot" in _plain(result.output)


def test_unknown_subcommand_fails() -> None:
    result = runner.invoke(app, ["ghost-cmd"])
    assert result.exit_code != 0


def test_upstream_subcommand_removed() -> None:
    """v0 閺嬭埖鐎▽鈩冩箒 upstream 濮掑倸搴?`chariot upstream` 鐎涙劕鎳℃禒銈呯安娑撳秴鐡ㄩ崷銊ｂ偓?""
    result = runner.invoke(app, ["upstream"])
    assert result.exit_code != 0


def test_chat_protocol_option_removed() -> None:
    """0.2.0 鐠у嘲宕熼崡蹇氼唴閸?`chat --protocol ...` 闁銆嶅韫瑓缁捐￥鈧?""
    result = runner.invoke(app, ["chat", "--protocol", "messages", "hi"])
    assert result.exit_code != 0


def test_chat_invalid_max_tokens_fails() -> None:
    """`--max-tokens` 韫囧懘銆忛弰?int;闂堢偞鏆熺€?typer 閼奉亜鐢?parser 闂冭埖顔岀亸杈ㄥГ闁挎瑣鈧?""
    result = runner.invoke(app, ["chat", "--max-tokens", "abc", "hi"])
    assert result.exit_code != 0


# ---------- --quiet 閸忋劌鐪?flag ----------


def test_quiet_flag_accepted_by_root_help() -> None:
    """閺?--help 闁插本婀?--quiet / -q 闁銆嶉妴?""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    out = _plain(result.output)
    assert "--quiet" in out
    assert "-q" in out


def test_quiet_flag_sets_renderer_state() -> None:
    """--quiet 鐟欙箑褰傞弽?callback 閸?Renderer.QUIET = True閵?""
    from chariot.cli.render import Renderer

    Renderer.QUIET = False  # 娣囨繈娅撴稉?
    # 閻劋绔存稉顏勭箑閻掕泛銇戠拹銉ф畱鐎涙劕鎳℃禒銈呮彥闁喕铔嬬€?callback + 鐎涙劕鎳℃禒銈呭棘閺佺増鐗庢?
    runner.invoke(app, ["--quiet", "chat", "--max-tokens", "abc", "hi"])
    assert Renderer.QUIET is True
    Renderer.QUIET = False  # 婢跺秳缍?闁灝鍘ゅЧ鈩冪厠閸氬海鐢?test


def test_short_quiet_flag() -> None:
    from chariot.cli.render import Renderer

    Renderer.QUIET = False
    runner.invoke(app, ["-q", "chat", "--max-tokens", "abc", "hi"])
    assert Renderer.QUIET is True
    Renderer.QUIET = False


def test_convo_delete_subcommand_visible() -> None:
    result = runner.invoke(app, ["conversation", "--help"])
    assert result.exit_code == 0
    assert "delete" in _plain(result.output)


def test_provider_delete_subcommand_visible() -> None:
    result = runner.invoke(app, ["provider", "--help"])
    assert result.exit_code == 0
    assert "delete" in _plain(result.output)

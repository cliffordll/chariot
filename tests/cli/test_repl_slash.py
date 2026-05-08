"""ChatRepl slash 命令测试(0.6.0 库化版)。

slash 命令分单 / 复数:**单数 = 显示当前状态(只读)**,**复数 = 列全部(走 repo)**。
覆盖 /model · /models · /convo · /convos · /tool · /tools。

REPL 主循环不测(input 阻塞难自动化);只针对 `_handle_slash` + `_slash_*`
方法,直接 await 调,断 ctx 状态变化 + Renderer 输出 + DB 形态变化。

每个 case 都用 tmp_path 起一个全新的 SQLite DB(`AIAgent.bootstrap` 跑
migrations + seed 默认 mock entry + 4 条 disabled tool fixture),保证测试隔离。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from chariot.agent.run import AIAgent
from chariot.cli.context import ChatContext
from chariot.cli.render import Renderer
from chariot.cli.repl import ChatRepl
from chariot.database.session import dispose_db
from chariot.repos.convo_repo import ConvoRepo
from chariot.repos.provider_repo import ProviderRepo
from chariot.repos.tool_repo import ToolRepo

# ============================================================
# fixtures
# ============================================================


@pytest_asyncio.fixture
async def agent(tmp_path: Path) -> AsyncIterator[AIAgent]:
    """每个 test 一个全新 DB + 全新 AIAgent;退出释放连接池避免文件锁残留。

    0.6.5 起 AIAgent 撤了单例,直接调 bootstrap 拿实例;teardown 清 DB engine。
    """
    db_path = tmp_path / "chariot.db"
    inst = await AIAgent.bootstrap(db_path)
    try:
        yield inst
    finally:
        await dispose_db()


def _make_ctx(agent: AIAgent, *, convo_id: str | None = None) -> ChatContext:
    return ChatContext(
        agent=agent,
        provider_name="claude-haiku-4-5",
        convo_id=convo_id,
    )


@pytest.fixture(autouse=True)
def _capture_renderer_output(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[list[tuple[str, str]], None, None]:
    """把 Renderer.out / error_bubble / table 改成在 list 里追加,方便断输出。

    第一元素是 channel(out / err / table),第二元素是核心内容。
    """
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(Renderer, "out", lambda msg: captured.append(("out", msg)))
    monkeypatch.setattr(Renderer, "error_bubble", lambda msg: captured.append(("err", msg)))

    def _fake_table(
        columns: list[str],
        rows: Any,
        *,
        title: str | None = None,
    ) -> None:
        flat = " | ".join(" ".join(str(cell) for cell in row) for row in rows)
        captured.append(("table", f"{title}::{flat}"))

    monkeypatch.setattr(Renderer, "table", _fake_table)
    yield captured


_Capt = list[tuple[str, str]]


# ==========================================================
# /provider · /providers
# ==========================================================


async def test_slash_provider_no_arg_shows_current(
    agent: AIAgent, _capture_renderer_output: _Capt
) -> None:
    """`/provider` 无参数:显示 ctx.provider_name,不查 DB。"""
    ctx = _make_ctx(agent)
    await ChatRepl(ctx=ctx)._handle_slash("/provider")
    outs = [c for c in _capture_renderer_output if c[0] == "out"]
    assert len(outs) == 1
    assert "claude-haiku-4-5" in outs[0][1]


async def test_slash_provider_with_name_sets_ctx_provider(agent: AIAgent) -> None:
    """`/provider <name>` 本次会话切到 entry(写到 ctx.provider_name,不动 DB)。"""
    ctx = _make_ctx(agent)
    await ChatRepl(ctx=ctx)._handle_slash("/provider new-entry-id")
    assert ctx.provider_name == "new-entry-id"
    # DB 默认未受影响
    async with agent.session_maker() as session:
        assert await ProviderRepo(session).get_default() is None


async def test_slash_provider_use_persists_default_to_db(agent: AIAgent) -> None:
    """`/provider use <name>` 设 DB 默认 + 同步本次 ctx 切到它。"""
    async with agent.session_maker() as session:
        await ProviderRepo(session).create(
            name="claude-haiku-4-5",
            type="anthropic",
            options={},
            params={},
        )
    ctx = _make_ctx(agent)  # provider_name=claude-haiku-4-5
    ctx.set_provider("mock")  # 先切到本地 mock
    await ChatRepl(ctx=ctx)._handle_slash("/provider use claude-haiku-4-5")

    assert ctx.provider_name == "claude-haiku-4-5"
    async with agent.session_maker() as session:
        default = await ProviderRepo(session).get_default()
    assert default is not None and default.name == "claude-haiku-4-5"


async def test_slash_provider_use_unknown_errors(
    agent: AIAgent, _capture_renderer_output: _Capt
) -> None:
    """`/provider use <unknown>` 报 error;ctx 不变。"""
    ctx = _make_ctx(agent)
    original = ctx.provider_name
    await ChatRepl(ctx=ctx)._handle_slash("/provider use ghost")
    errs = [c for c in _capture_renderer_output if c[0] == "err"]
    assert len(errs) == 1
    assert ctx.provider_name == original


async def test_slash_providers_lists_entries_with_current_and_default_markers(
    agent: AIAgent, _capture_renderer_output: _Capt
) -> None:
    """`/providers` 走 ProviderRepo,带 default `*` + ← current 标记。

    fresh DB 默认 seed 1 条 mock entry;手动加一条 claude-haiku-4-5 当前 provider 且设默认。
    """
    async with agent.session_maker() as session:
        repo = ProviderRepo(session)
        await repo.create(
            name="claude-haiku-4-5",
            type="anthropic",
            options={},
            params={},
        )
        await repo.set_default("claude-haiku-4-5")

    ctx = _make_ctx(agent)  # ctx.provider_name = claude-haiku-4-5
    await ChatRepl(ctx=ctx)._handle_slash("/providers")

    tables = [c for c in _capture_renderer_output if c[0] == "table"]
    assert len(tables) == 1
    title, body = tables[0][1].split("::", 1)
    assert title == "entries"
    assert "claude-haiku-4-5" in body
    assert "← current" in body
    assert body.count("← current") == 1
    # default 标记 `*` 也只一条
    assert " * " in body or body.endswith(" *") or "* " in body
    # mock 也在表里(seeded)
    assert "mock" in body


async def test_slash_providers_with_arg_errors(
    agent: AIAgent, _capture_renderer_output: _Capt
) -> None:
    """`/providers <arg>` 报 error 引导用 `/provider <name>` 切 provider。"""
    ctx = _make_ctx(agent)
    await ChatRepl(ctx=ctx)._handle_slash("/providers foo")
    errs = [c for c in _capture_renderer_output if c[0] == "err"]
    assert len(errs) == 1
    assert "/provider" in errs[0][1]


# ==========================================================
# /convo · /convos
# ==========================================================


CONVO = "01ABCDEF0123456789ABCDEFGH"


async def test_slash_convo_no_arg_shows_current_with_id(
    agent: AIAgent, _capture_renderer_output: _Capt
) -> None:
    """有 convo_id 时,`/convo` 显示 id + model + 本地消息数,不查 DB。"""
    ctx = _make_ctx(agent, convo_id=CONVO)
    ctx.append_user("hi")
    await ChatRepl(ctx=ctx)._handle_slash("/convo")
    outs = [c for c in _capture_renderer_output if c[0] == "out"]
    assert len(outs) == 1
    msg = outs[0][1]
    assert CONVO in msg
    assert "claude-haiku-4-5" in msg
    assert "1" in msg  # local msgs=1


async def test_slash_convo_no_arg_stateless_shows_off(
    agent: AIAgent, _capture_renderer_output: _Capt
) -> None:
    """stateless(convo_id=None)时 `/convo` 显示 off。"""
    ctx = _make_ctx(agent)
    await ChatRepl(ctx=ctx)._handle_slash("/convo")
    outs = [c for c in _capture_renderer_output if c[0] == "out"]
    assert len(outs) == 1
    msg = outs[0][1].lower()
    assert "off" in msg or "stateless" in msg


async def test_slash_convos_lists_with_current_marker(
    agent: AIAgent, _capture_renderer_output: _Capt
) -> None:
    """`/convos` 走 ConvoRepo,当前会话行带 ← current。"""
    other_id = "01ZZZZZZZZZZZZZZZZZZZZZZZZ"
    async with agent.session_maker() as session:
        repo = ConvoRepo(session)
        await repo.create(CONVO, title="t1")
        await repo.create(other_id, title="t2")

    ctx = _make_ctx(agent, convo_id=CONVO)
    await ChatRepl(ctx=ctx)._handle_slash("/convos")
    tables = [c for c in _capture_renderer_output if c[0] == "table"]
    assert len(tables) == 1
    body = tables[0][1].split("::", 1)[1]
    assert "← current" in body
    # ← current 只出现一次(只有 ctx 当前会话)
    assert body.count("← current") == 1
    assert CONVO in body and other_id in body


async def test_slash_convos_empty_db_shows_hint(
    agent: AIAgent, _capture_renderer_output: _Capt
) -> None:
    """空 DB → `/convos` 提示用 `/convo new` 开一个。"""
    ctx = _make_ctx(agent)
    await ChatRepl(ctx=ctx)._handle_slash("/convos")
    outs = [c for c in _capture_renderer_output if c[0] == "out"]
    assert len(outs) == 1
    assert "/convo new" in outs[0][1]


async def test_slash_convos_with_arg_errors(
    agent: AIAgent, _capture_renderer_output: _Capt
) -> None:
    """`/convos <arg>` 报 error 引导用 `/convo <ULID|new|off>`。"""
    ctx = _make_ctx(agent)
    await ChatRepl(ctx=ctx)._handle_slash("/convos new")
    errs = [c for c in _capture_renderer_output if c[0] == "err"]
    assert len(errs) == 1
    assert "/convo" in errs[0][1]


async def test_slash_convo_new_generates_ulid(agent: AIAgent) -> None:
    ctx = _make_ctx(agent)
    ctx.append_user("旧消息")  # 切会话前留一条历史
    await ChatRepl(ctx=ctx)._handle_slash("/convo new")
    assert ctx.convo_id is not None
    assert len(ctx.convo_id) == 26
    # 切会话清空本地 messages
    assert ctx.messages == []


async def test_slash_convo_with_ulid_sets_and_clears(agent: AIAgent) -> None:
    ulid = "01HXXXXXXXXXXXXXXXXXXXXXXX"
    ctx = _make_ctx(agent)
    ctx.append_user("旧消息")
    await ChatRepl(ctx=ctx)._handle_slash(f"/convo {ulid}")
    assert ctx.convo_id == ulid
    assert ctx.messages == []


async def test_slash_convo_off_clears_id_and_messages(agent: AIAgent) -> None:
    ctx = _make_ctx(agent, convo_id=CONVO)
    ctx.append_user("hi")
    await ChatRepl(ctx=ctx)._handle_slash("/convo off")
    assert ctx.convo_id is None
    assert ctx.messages == []


async def test_slash_convo_invalid_value_errors(
    agent: AIAgent, _capture_renderer_output: _Capt
) -> None:
    ctx = _make_ctx(agent)
    await ChatRepl(ctx=ctx)._handle_slash("/convo foo")
    assert ctx.convo_id is None
    errs = [c for c in _capture_renderer_output if c[0] == "err"]
    assert len(errs) == 1
    assert "ULID" in errs[0][1]


# ==========================================================
# /tool · /tools
# ==========================================================


async def test_slash_tool_shows_only_enabled(
    agent: AIAgent, _capture_renderer_output: _Capt
) -> None:
    """`/tool` 只列 enabled=True;disabled 的不出现在表里。

    fresh DB seed 4 条全 disabled 的 fixture,先开 read_file 再断。
    """
    async with agent.session_maker() as session:
        await ToolRepo(session).update("read_file", enabled=True)

    ctx = _make_ctx(agent)
    await ChatRepl(ctx=ctx)._handle_slash("/tool")
    tables = [c for c in _capture_renderer_output if c[0] == "table"]
    assert len(tables) == 1
    title, body = tables[0][1].split("::", 1)
    assert "enabled" in title
    assert "read_file" in body
    # disabled 的不在 enabled 视图里
    assert "shell_exec" not in body


async def test_slash_tool_no_enabled_shows_hint(
    agent: AIAgent, _capture_renderer_output: _Capt
) -> None:
    """全部 disabled → `/tool` 提示 `chariot tool enable`。"""
    ctx = _make_ctx(agent)
    await ChatRepl(ctx=ctx)._handle_slash("/tool")
    outs = [c for c in _capture_renderer_output if c[0] == "out"]
    assert len(outs) == 1
    assert "chariot tool enable" in outs[0][1]


async def test_slash_tools_lists_all_with_marks(
    agent: AIAgent, _capture_renderer_output: _Capt
) -> None:
    """`/tools` 列全部工具,enabled / disabled 都展示并带 ON/off 标记。"""
    async with agent.session_maker() as session:
        await ToolRepo(session).update("read_file", enabled=True)

    ctx = _make_ctx(agent)
    await ChatRepl(ctx=ctx)._handle_slash("/tools")
    tables = [c for c in _capture_renderer_output if c[0] == "table"]
    assert len(tables) == 1
    body = tables[0][1].split("::", 1)[1]
    assert "ON" in body and "off" in body
    assert "read_file" in body and "shell_exec" in body


async def test_slash_tool_with_arg_errors(agent: AIAgent, _capture_renderer_output: _Capt) -> None:
    """`/tool <arg>` 报 error 引导用 chariot tool 子命令组(REPL 内不改启用状态)。"""
    ctx = _make_ctx(agent)
    await ChatRepl(ctx=ctx)._handle_slash("/tool enable read_file")
    errs = [c for c in _capture_renderer_output if c[0] == "err"]
    assert len(errs) == 1
    assert "chariot tool" in errs[0][1]


async def test_slash_tools_with_arg_errors(agent: AIAgent, _capture_renderer_output: _Capt) -> None:
    """`/tools <arg>` 同样不接受参数。"""
    ctx = _make_ctx(agent)
    await ChatRepl(ctx=ctx)._handle_slash("/tools enable")
    errs = [c for c in _capture_renderer_output if c[0] == "err"]
    assert len(errs) == 1
    assert "chariot tool" in errs[0][1]


# ==========================================================
# 其它命令保留
# ==========================================================


async def test_slash_exit_returns_true(agent: AIAgent) -> None:
    ctx = _make_ctx(agent)
    assert await ChatRepl(ctx=ctx)._handle_slash("/exit") is True
    assert await ChatRepl(ctx=ctx)._handle_slash("/quit") is True


async def test_slash_unknown_errors(agent: AIAgent, _capture_renderer_output: _Capt) -> None:
    ctx = _make_ctx(agent)
    await ChatRepl(ctx=ctx)._handle_slash("/nope")
    errs = [c for c in _capture_renderer_output if c[0] == "err"]
    assert len(errs) == 1
    assert "未知命令" in errs[0][1]

"""ChatRepl slash 命令测试(0.6.0 库化版)。

slash 命令分单 / 复数:**单数 = 显示当前状态(只读)**,**复数 = 列全部(走 repo)**。
覆盖 /agent · /agents · /convo · /convos · /tool · /tools。

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
from chariot.repos.conversation_repo import ConversationRepo
from chariot.repos.task_repo import TaskRepo
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


def _make_ctx(agent: AIAgent, *, conversation_id: str | None = None) -> ChatContext:
    return ChatContext(
        agent=agent,
        provider_name="claude-haiku-4-5",
        conversation_id=conversation_id,
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
# /agent · /agents
# ==========================================================


async def test_slash_agent_no_arg_shows_current(agent: AIAgent, _capture_renderer_output: _Capt) -> None:
    """`/agent` 无参数:显示 ctx.agent_profile。"""
    ctx = _make_ctx(agent)
    await ChatRepl(ctx=ctx)._handle_slash("/agent")
    outs = [c for c in _capture_renderer_output if c[0] == "out"]
    assert len(outs) == 1
    assert "(none)" in outs[0][1]


async def test_slash_agent_with_name_sets_ctx_agent_profile(agent: AIAgent) -> None:
    """`/agent <name>` 设置 ctx.agent_profile。"""
    ctx = _make_ctx(agent)
    await ChatRepl(ctx=ctx)._handle_slash("/agent dev-helper")
    assert ctx.agent_profile == "dev-helper"


async def test_slash_agent_clear_clears_profile(agent: AIAgent) -> None:
    """`/agent clear` 清空 ctx.agent_profile。"""
    ctx = _make_ctx(agent)
    ctx.agent_profile = "dev-helper"
    await ChatRepl(ctx=ctx)._handle_slash("/agent clear")
    assert ctx.agent_profile is None


async def test_slash_agents_lists_entries_with_current_marker(agent: AIAgent, _capture_renderer_output: _Capt) -> None:
    """`/agents` 走 AgentService,带 ← current 标记。"""
    async with agent.session_maker() as session:
        from chariot.services.agent import AgentService

        await AgentService(TaskRepo(session)).create_agent(
            name="dev-helper",
            role="developer",
        )

    ctx = _make_ctx(agent)
    ctx.agent_profile = "dev-helper"
    await ChatRepl(ctx=ctx)._handle_slash("/agents")

    tables = [c for c in _capture_renderer_output if c[0] == "table"]
    assert len(tables) == 1
    title, body = tables[0][1].split("::", 1)
    assert title == "agent profiles"
    assert "dev-helper" in body
    assert "← current" in body
    assert body.count("← current") == 1


async def test_slash_agents_with_arg_errors(agent: AIAgent, _capture_renderer_output: _Capt) -> None:
    """`/agents <arg>` 报 error 引导用 `/agent <name>` 切 agent。"""
    ctx = _make_ctx(agent)
    await ChatRepl(ctx=ctx)._handle_slash("/agents foo")
    errs = [c for c in _capture_renderer_output if c[0] == "err"]
    assert len(errs) == 1
    assert "/agent" in errs[0][1]


# ==========================================================
# /convo · /convos
# ==========================================================


CONVO = "01ABCDEF0123456789ABCDEFGH"


async def test_slash_convo_no_arg_shows_current_with_id(agent: AIAgent, _capture_renderer_output: _Capt) -> None:
    """有 conversation_id 时,`/convo` 显示 id + model + 本地消息数,不查 DB。"""
    ctx = _make_ctx(agent, conversation_id=CONVO)
    ctx.append_user("hi")
    await ChatRepl(ctx=ctx)._handle_slash("/conversation")
    outs = [c for c in _capture_renderer_output if c[0] == "out"]
    assert len(outs) == 1
    msg = outs[0][1]
    assert CONVO in msg
    assert "claude-haiku-4-5" in msg
    assert "1" in msg  # local msgs=1


async def test_slash_convo_no_arg_stateless_shows_off(agent: AIAgent, _capture_renderer_output: _Capt) -> None:
    """stateless(conversation_id=None)时 `/convo` 显示 off。"""
    ctx = _make_ctx(agent)
    await ChatRepl(ctx=ctx)._handle_slash("/conversation")
    outs = [c for c in _capture_renderer_output if c[0] == "out"]
    assert len(outs) == 1
    msg = outs[0][1].lower()
    assert "off" in msg or "stateless" in msg


async def test_slash_convos_lists_with_current_marker(agent: AIAgent, _capture_renderer_output: _Capt) -> None:
    """`/convos` 走 ConversationRepo,当前会话行带 ← current。"""
    other_id = "01ZZZZZZZZZZZZZZZZZZZZZZZZ"
    async with agent.session_maker() as session:
        repo = ConversationRepo(session)
        await repo.create(CONVO, title="t1")
        await repo.create(other_id, title="t2")

    ctx = _make_ctx(agent, conversation_id=CONVO)
    await ChatRepl(ctx=ctx)._handle_slash("/conversations")
    tables = [c for c in _capture_renderer_output if c[0] == "table"]
    assert len(tables) == 1
    body = tables[0][1].split("::", 1)[1]
    assert "← current" in body
    # ← current 只出现一次(只有 ctx 当前会话)
    assert body.count("← current") == 1
    assert CONVO in body and other_id in body


async def test_slash_convos_empty_db_shows_hint(agent: AIAgent, _capture_renderer_output: _Capt) -> None:
    """空 DB → `/convos` 提示用 `/convo new` 开一个。"""
    ctx = _make_ctx(agent)
    await ChatRepl(ctx=ctx)._handle_slash("/conversations")
    outs = [c for c in _capture_renderer_output if c[0] == "out"]
    assert len(outs) == 1
    assert "/conversation new" in outs[0][1]


async def test_slash_convos_with_arg_errors(agent: AIAgent, _capture_renderer_output: _Capt) -> None:
    """`/convos <arg>` 报 error 引导用 `/convo <ULID|new|off>`。"""
    ctx = _make_ctx(agent)
    await ChatRepl(ctx=ctx)._handle_slash("/convos new")
    errs = [c for c in _capture_renderer_output if c[0] == "err"]
    assert len(errs) == 1
    assert "/conversation" in errs[0][1]


async def test_slash_convo_new_generates_ulid(agent: AIAgent) -> None:
    ctx = _make_ctx(agent)
    ctx.append_user("旧消息")  # 切会话前留一条历史
    await ChatRepl(ctx=ctx)._handle_slash("/conversation new")
    assert ctx.conversation_id is not None
    assert len(ctx.conversation_id) == 26
    # 切会话清空本地 messages
    assert ctx.messages == []


async def test_slash_convo_with_ulid_sets_and_clears(agent: AIAgent) -> None:
    ulid = "01HXXXXXXXXXXXXXXXXXXXXXXX"
    ctx = _make_ctx(agent)
    ctx.append_user("旧消息")
    await ChatRepl(ctx=ctx)._handle_slash(f"/convo {ulid}")
    assert ctx.conversation_id == ulid
    assert ctx.messages == []


async def test_slash_convo_off_clears_id_and_messages(agent: AIAgent) -> None:
    ctx = _make_ctx(agent, conversation_id=CONVO)
    ctx.append_user("hi")
    await ChatRepl(ctx=ctx)._handle_slash("/convo off")
    assert ctx.conversation_id is None
    assert ctx.messages == []


async def test_slash_convo_invalid_value_errors(agent: AIAgent, _capture_renderer_output: _Capt) -> None:
    ctx = _make_ctx(agent)
    await ChatRepl(ctx=ctx)._handle_slash("/convo foo")
    assert ctx.conversation_id is None
    errs = [c for c in _capture_renderer_output if c[0] == "err"]
    assert len(errs) == 1
    assert "ULID" in errs[0][1]


async def test_slash_help_shows_conversation_aliases(agent: AIAgent, _capture_renderer_output: _Capt) -> None:
    ctx = _make_ctx(agent)
    await ChatRepl(ctx=ctx)._handle_slash("/help")
    outs = [c for c in _capture_renderer_output if c[0] == "out"]
    assert len(outs) == 1
    msg = outs[0][1]
    assert "/conversation, /convo" in msg
    assert "/conversations, /convos" in msg
    assert "/conversation new" in msg
    assert "/convo new" in msg
    assert "/conversation <ULID>" in msg
    assert "/convo <ULID>" in msg
    assert "/conversation off" in msg
    assert "/convo off" in msg


# ==========================================================
# /tool · /tools
# ==========================================================


async def test_slash_tool_shows_only_enabled(agent: AIAgent, _capture_renderer_output: _Capt) -> None:
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


async def test_slash_tool_no_enabled_shows_hint(agent: AIAgent, _capture_renderer_output: _Capt) -> None:
    """全部 disabled → `/tool` 提示 `chariot tool enable`。"""
    ctx = _make_ctx(agent)
    await ChatRepl(ctx=ctx)._handle_slash("/tool")
    outs = [c for c in _capture_renderer_output if c[0] == "out"]
    assert len(outs) == 1
    assert "chariot tool enable" in outs[0][1]


async def test_slash_tools_lists_all_with_marks(agent: AIAgent, _capture_renderer_output: _Capt) -> None:
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

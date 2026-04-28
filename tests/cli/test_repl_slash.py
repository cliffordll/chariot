"""ChatRepl slash 命令测试。

0.4.0 起 slash 命令分单 / 复数:**单数 = 显示当前状态(只读 / 不打 server 或只 enabled)**,
**复数 = 列全部(走 server list API)**。覆盖 /model · /models · /conversation ·
/conversations · /tool · /tools。

REPL 主循环不测(input 阻塞难自动化);只针对 `_handle_slash` + `_slash_*`
方法,直接 await 调,断 ctx 状态变化 + Renderer 输出 + 是否调对了 client API。
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chariot.cli.core.context import ChatContext
from chariot.cli.core.render import Renderer
from chariot.cli.core.repl import ChatRepl
from chariot.server.controller.conversations import (
    ConversationOut,
    ConversationsListResponse,
)
from chariot.server.controller.models import EntryResponse, ModelsListResponse
from chariot.server.controller.tools import ToolOut, ToolsListResponse


def _make_ctx(*, conversation_id: str | None = None) -> ChatContext:
    """构造一个 ChatContext,client 是 MagicMock(各 list_* 方法用 AsyncMock 注)。"""
    client = MagicMock()
    return ChatContext(
        client=client,
        model="claude-haiku-4-5",
        conversation_id=conversation_id,
    )


@pytest.fixture(autouse=True)
def _capture_renderer_output(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, str]]:
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
        # 只关心 title + 行数 + 行内字段;断言时按需 join 行
        flat = " | ".join(" ".join(str(cell) for cell in row) for row in rows)
        captured.append(("table", f"{title}::{flat}"))

    monkeypatch.setattr(Renderer, "table", _fake_table)
    return captured


# ==========================================================
# /model · /models
# ==========================================================


async def test_slash_model_no_arg_shows_current(
    _capture_renderer_output: list[tuple[str, str]],
) -> None:
    """`/model` 无参数:显示 ctx.model,不打 server。"""
    ctx = _make_ctx()
    ctx.client.list_models = AsyncMock()  # type: ignore[attr-defined]
    await ChatRepl(ctx=ctx)._handle_slash("/model")
    ctx.client.list_models.assert_not_awaited()
    outs = [c for c in _capture_renderer_output if c[0] == "out"]
    assert len(outs) == 1
    assert "claude-haiku-4-5" in outs[0][1]


async def test_slash_model_with_name_sets_ctx_model() -> None:
    """`/model <name>` 切到指定 entry(本地状态)。"""
    ctx = _make_ctx()
    await ChatRepl(ctx=ctx)._handle_slash("/model new-model-id")
    assert ctx.model == "new-model-id"


async def test_slash_models_lists_entries(
    _capture_renderer_output: list[tuple[str, str]],
) -> None:
    """`/models` 走 list_models API,带 ← current 标记。"""
    ctx = _make_ctx()
    ctx.client.list_models = AsyncMock(  # type: ignore[attr-defined]
        return_value=ModelsListResponse(
            available=["mock", "claude-haiku-4-5"],
            types=["mock", "anthropic"],
            entries=[
                EntryResponse(name="mock", type="mock", options={}, params={}),
                EntryResponse(
                    name="claude-haiku-4-5",
                    type="anthropic",
                    options={},
                    params={},
                ),
            ],
        ),
    )
    await ChatRepl(ctx=ctx)._handle_slash("/models")
    ctx.client.list_models.assert_awaited_once()
    tables = [c for c in _capture_renderer_output if c[0] == "table"]
    assert len(tables) == 1
    title, body = tables[0][1].split("::", 1)
    assert title == "entries"
    # current 标记:claude-haiku-4-5 行带 ← current
    assert "claude-haiku-4-5" in body and "← current" in body
    # mock 行没有 ← current(整张表只一处)
    assert body.count("← current") == 1


async def test_slash_models_with_arg_errors(
    _capture_renderer_output: list[tuple[str, str]],
) -> None:
    """`/models <arg>` 报 error 引导用 `/model <name>` 切模型。"""
    ctx = _make_ctx()
    await ChatRepl(ctx=ctx)._handle_slash("/models foo")
    errs = [c for c in _capture_renderer_output if c[0] == "err"]
    assert len(errs) == 1
    assert "/model" in errs[0][1]


# ==========================================================
# /conversation · /conversations
# ==========================================================


async def test_slash_conversation_no_arg_shows_current_with_id(
    _capture_renderer_output: list[tuple[str, str]],
) -> None:
    """有 conversation_id 时,`/conversation` 显示 id + model + 本地消息数,不打 server。"""
    ctx = _make_ctx(conversation_id="01ABCDEF0123456789ABCDEFGH")
    ctx.append_user("hi")
    ctx.client.list_conversations = AsyncMock()  # type: ignore[attr-defined]
    await ChatRepl(ctx=ctx)._handle_slash("/conversation")
    ctx.client.list_conversations.assert_not_awaited()
    outs = [c for c in _capture_renderer_output if c[0] == "out"]
    assert len(outs) == 1
    msg = outs[0][1]
    assert "01ABCDEF0123456789ABCDEFGH" in msg
    assert "claude-haiku-4-5" in msg
    # local msgs=1 ↘ 数字串得在
    assert "1" in msg


async def test_slash_conversation_no_arg_stateless_shows_off(
    _capture_renderer_output: list[tuple[str, str]],
) -> None:
    """stateless(conversation_id=None)时 `/conversation` 显示 off。"""
    ctx = _make_ctx()
    await ChatRepl(ctx=ctx)._handle_slash("/conversation")
    outs = [c for c in _capture_renderer_output if c[0] == "out"]
    assert len(outs) == 1
    msg = outs[0][1].lower()
    assert "off" in msg or "stateless" in msg


async def test_slash_conversations_lists(
    _capture_renderer_output: list[tuple[str, str]],
) -> None:
    """`/conversations` 走 list_conversations API,当前会话行带 ← current。"""
    ctx = _make_ctx(conversation_id="01ABCDEF0123456789ABCDEFGH")
    now = dt.datetime(2026, 4, 28, 10, 0, 0, tzinfo=dt.UTC)
    ctx.client.list_conversations = AsyncMock(  # type: ignore[attr-defined]
        return_value=ConversationsListResponse(
            items=[
                ConversationOut(
                    id="01ABCDEF0123456789ABCDEFGH",
                    title="t1",
                    last_model="mock",
                    message_count=2,
                    created_at=now,
                    updated_at=now,
                ),
                ConversationOut(
                    id="01ZZZZZZZZZZZZZZZZZZZZZZZZ",
                    title=None,
                    last_model=None,
                    message_count=0,
                    created_at=now,
                    updated_at=now,
                ),
            ],
            limit=20,
            offset=0,
        ),
    )
    await ChatRepl(ctx=ctx)._handle_slash("/conversations")
    ctx.client.list_conversations.assert_awaited_once_with(limit=20)
    tables = [c for c in _capture_renderer_output if c[0] == "table"]
    assert len(tables) == 1
    body = tables[0][1].split("::", 1)[1]
    # current 标记应在第一行(id 跟 ctx.conversation_id 一致)
    first_row = body.split(" | ")[0]
    assert "← current" in first_row


async def test_slash_conversations_with_arg_errors(
    _capture_renderer_output: list[tuple[str, str]],
) -> None:
    """`/conversations <arg>` 报 error 引导用 `/conversation <ULID|new|off>`。"""
    ctx = _make_ctx()
    await ChatRepl(ctx=ctx)._handle_slash("/conversations new")
    errs = [c for c in _capture_renderer_output if c[0] == "err"]
    assert len(errs) == 1
    assert "/conversation" in errs[0][1]


async def test_slash_conversation_new_generates_ulid() -> None:
    ctx = _make_ctx()
    ctx.append_user("旧消息")  # 切会话前留一条历史
    await ChatRepl(ctx=ctx)._handle_slash("/conversation new")
    assert ctx.conversation_id is not None
    assert len(ctx.conversation_id) == 26
    # 切会话清空本地 messages(server 是新会话历史的真源)
    assert ctx.messages == []


async def test_slash_conversation_with_ulid_sets_and_clears() -> None:
    ulid = "01HXXXXXXXXXXXXXXXXXXXXXXX"
    ctx = _make_ctx()
    ctx.append_user("旧消息")
    await ChatRepl(ctx=ctx)._handle_slash(f"/conversation {ulid}")
    assert ctx.conversation_id == ulid
    assert ctx.messages == []


async def test_slash_conversation_off_clears_id_and_messages() -> None:
    ctx = _make_ctx(conversation_id="01ABCDEF0123456789ABCDEFGH")
    ctx.append_user("hi")
    await ChatRepl(ctx=ctx)._handle_slash("/conversation off")
    assert ctx.conversation_id is None
    assert ctx.messages == []


async def test_slash_conversation_invalid_value_errors(
    _capture_renderer_output: list[tuple[str, str]],
) -> None:
    ctx = _make_ctx()
    await ChatRepl(ctx=ctx)._handle_slash("/conversation foo")
    # 没改 conv_id
    assert ctx.conversation_id is None
    # 报 error_bubble
    errs = [c for c in _capture_renderer_output if c[0] == "err"]
    assert len(errs) == 1
    assert "ULID" in errs[0][1]


# ==========================================================
# /tool · /tools
# ==========================================================


def _tools_response_mixed() -> ToolsListResponse:
    """一个 enabled + 一个 disabled,用于 `/tool`(只看 enabled)和 `/tools`(全部)对比。"""
    return ToolsListResponse(
        types=["read_file", "list_dir", "shell_exec", "http_get"],
        entries=[
            ToolOut(
                name="read_file",
                type="read_file",
                enabled=True,
                options={"max_bytes": 1048576},
                schema_=None,
            ),
            ToolOut(
                name="shell_exec",
                type="shell_exec",
                enabled=False,
                options={"workdir": "~/.chariot/sandbox"},
                schema_=None,
            ),
        ],
    )


async def test_slash_tool_shows_only_enabled(
    _capture_renderer_output: list[tuple[str, str]],
) -> None:
    """`/tool` 只列 enabled=True;disabled 的不出现在表里。"""
    ctx = _make_ctx()
    ctx.client.list_tools = AsyncMock(return_value=_tools_response_mixed())  # type: ignore[attr-defined]
    await ChatRepl(ctx=ctx)._handle_slash("/tool")
    ctx.client.list_tools.assert_awaited_once()
    tables = [c for c in _capture_renderer_output if c[0] == "table"]
    assert len(tables) == 1
    title, body = tables[0][1].split("::", 1)
    assert "enabled" in title
    assert "read_file" in body
    # disabled 的工具不在 enabled 视图里
    assert "shell_exec" not in body


async def test_slash_tools_lists_all(
    _capture_renderer_output: list[tuple[str, str]],
) -> None:
    """`/tools` 列全部工具,enabled / disabled 都展示并带 ON/off 标记。"""
    ctx = _make_ctx()
    ctx.client.list_tools = AsyncMock(return_value=_tools_response_mixed())  # type: ignore[attr-defined]
    await ChatRepl(ctx=ctx)._handle_slash("/tools")
    ctx.client.list_tools.assert_awaited_once()
    tables = [c for c in _capture_renderer_output if c[0] == "table"]
    assert len(tables) == 1
    body = tables[0][1].split("::", 1)[1]
    assert "ON" in body and "off" in body
    assert "read_file" in body and "shell_exec" in body


async def test_slash_tool_with_arg_errors(
    _capture_renderer_output: list[tuple[str, str]],
) -> None:
    """`/tool <arg>` 报 error 引导用 chariot tool 子命令组(REPL 内不改启用状态)。"""
    ctx = _make_ctx()
    await ChatRepl(ctx=ctx)._handle_slash("/tool enable read_file")
    errs = [c for c in _capture_renderer_output if c[0] == "err"]
    assert len(errs) == 1
    assert "chariot tool" in errs[0][1]


async def test_slash_tools_with_arg_errors(
    _capture_renderer_output: list[tuple[str, str]],
) -> None:
    """`/tools <arg>` 同样不接受参数。"""
    ctx = _make_ctx()
    await ChatRepl(ctx=ctx)._handle_slash("/tools enable")
    errs = [c for c in _capture_renderer_output if c[0] == "err"]
    assert len(errs) == 1
    assert "chariot tool" in errs[0][1]


# ==========================================================
# 其它命令保留
# ==========================================================


async def test_slash_exit_returns_true() -> None:
    ctx = _make_ctx()
    assert await ChatRepl(ctx=ctx)._handle_slash("/exit") is True
    assert await ChatRepl(ctx=ctx)._handle_slash("/quit") is True


async def test_slash_unknown_errors(
    _capture_renderer_output: list[tuple[str, str]],
) -> None:
    ctx = _make_ctx()
    await ChatRepl(ctx=ctx)._handle_slash("/nope")
    errs = [c for c in _capture_renderer_output if c[0] == "err"]
    assert len(errs) == 1
    assert "未知命令" in errs[0][1]

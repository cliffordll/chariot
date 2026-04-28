"""ChatContext.refresh_from_server 测试(0.5.0 S.3)。

跨客户端协调:UI / 第二 CLI session 往同一 conversation_id 写后,本轮 send
前 refresh,本地 self.messages 应被 server canonical history 整段替换。

覆盖:
- stateless(`conversation_id is None`):no-op,不打 server
- stateful + server 有历史:整段替换 self.messages,role / content 透传
- stateful + content blocks(非纯文本):content 透传 list(包 tool_use / tool_result)
- stateful + 网络 / 4xx 错:静默吞,self.messages 不动(不阻塞 send)
- REPL._one_turn 在 send 前调 refresh(env 默认开)
- env CHARIOT_CLI_AUTO_REFRESH=0 → REPL 跳过 refresh
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from chariot.cli.core.context import ChatContext
from chariot.cli.core.render import Renderer
from chariot.cli.core.repl import ChatRepl
from chariot.server.controller.conversations import (
    ConversationDetailResponse,
    ConversationOut,
    MessageOut,
)


def _make_ctx(*, conversation_id: str | None = None) -> ChatContext:
    return ChatContext(
        client=MagicMock(),
        model="claude-haiku-4-5",
        conversation_id=conversation_id,
    )


def _detail(messages: list[MessageOut], conv_id: str) -> ConversationDetailResponse:
    now = dt.datetime(2026, 4, 28, 10, 0, 0, tzinfo=dt.UTC)
    return ConversationDetailResponse(
        conversation=ConversationOut(
            id=conv_id,
            title=None,
            last_model="claude-haiku-4-5",
            message_count=len(messages),
            created_at=now,
            updated_at=now,
        ),
        messages=messages,
    )


# ============================================================
# stateless:no-op
# ============================================================


async def test_refresh_stateless_is_noop_does_not_call_server() -> None:
    """conversation_id is None → 直接 return,不打 server。"""
    ctx = _make_ctx()
    ctx.append_user("hi")
    ctx.client.get_conversation = AsyncMock()  # type: ignore[attr-defined]
    await ctx.refresh_from_server()
    ctx.client.get_conversation.assert_not_awaited()
    # messages 不动
    assert ctx.messages == [{"role": "user", "content": "hi"}]


# ============================================================
# stateful:整段替换
# ============================================================


CONV = "01ABCDEF0123456789ABCDEFGH"


async def test_refresh_stateful_replaces_local_messages_with_server() -> None:
    """server 返 [u1, a1, u2],本地 [本地 u3] 整段被替换。"""
    now = dt.datetime(2026, 4, 28, 10, 0, 0, tzinfo=dt.UTC)
    ctx = _make_ctx(conversation_id=CONV)
    ctx.append_user("本地的 u3")  # 本地有一条
    server_msgs = [
        MessageOut(seq=1, role="user", content="u1", model_name=None, created_at=now),
        MessageOut(
            seq=2, role="assistant", content="a1", model_name="claude-haiku-4-5", created_at=now
        ),
        MessageOut(seq=3, role="user", content="u2", model_name=None, created_at=now),
    ]
    ctx.client.get_conversation = AsyncMock(return_value=_detail(server_msgs, CONV))  # type: ignore[attr-defined]

    await ctx.refresh_from_server()

    ctx.client.get_conversation.assert_awaited_once_with(CONV)
    assert ctx.messages == [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
    ]


async def test_refresh_stateful_with_content_blocks_passthrough() -> None:
    """server 返 content 是 anthropic blocks 数组(tool_use / tool_result),原样透传。"""
    now = dt.datetime(2026, 4, 28, 10, 0, 0, tzinfo=dt.UTC)
    ctx = _make_ctx(conversation_id=CONV)
    blocks: list[dict[str, Any]] = [
        {"type": "text", "text": "让我查一下"},
        {"type": "tool_use", "id": "tu_1", "name": "read_file", "input": {"path": "x.txt"}},
    ]
    server_msgs = [
        MessageOut(
            seq=1,
            role="assistant",
            content=blocks,
            model_name="claude-haiku-4-5",
            created_at=now,
        ),
    ]
    ctx.client.get_conversation = AsyncMock(return_value=_detail(server_msgs, CONV))  # type: ignore[attr-defined]

    await ctx.refresh_from_server()

    assert ctx.messages == [{"role": "assistant", "content": blocks}]


async def test_refresh_stateful_empty_server_history_clears_local() -> None:
    """server 返空(刚创建的 conv)→ 本地 messages 清空。"""
    ctx = _make_ctx(conversation_id=CONV)
    ctx.append_user("本地遗留")
    ctx.client.get_conversation = AsyncMock(return_value=_detail([], CONV))  # type: ignore[attr-defined]

    await ctx.refresh_from_server()
    assert ctx.messages == []


# ============================================================
# 错误降级:静默吞,不阻塞 send
# ============================================================


async def test_refresh_swallows_http_error_local_messages_unchanged() -> None:
    """server 4xx / 网络断 → 静默吞,本地 messages 不动(不阻塞用户发消息)。"""
    ctx = _make_ctx(conversation_id=CONV)
    ctx.append_user("local")
    ctx.client.get_conversation = AsyncMock(  # type: ignore[attr-defined]
        side_effect=httpx.HTTPStatusError(
            "404",
            request=MagicMock(),
            response=MagicMock(status_code=404),
        ),
    )
    await ctx.refresh_from_server()  # 不抛
    assert ctx.messages == [{"role": "user", "content": "local"}]


async def test_refresh_swallows_network_error() -> None:
    ctx = _make_ctx(conversation_id=CONV)
    ctx.client.get_conversation = AsyncMock(  # type: ignore[attr-defined]
        side_effect=httpx.ConnectError("server down"),
    )
    await ctx.refresh_from_server()  # 不抛
    assert ctx.messages == []


# ============================================================
# REPL._one_turn 集成:send 前调 refresh
# ============================================================


@pytest.fixture(autouse=True)
def _silence_renderer(monkeypatch: pytest.MonkeyPatch) -> None:
    """REPL._one_turn 走 Renderer 多个 sink,测试不关心输出形态,全部静默。"""
    for name in (
        "stream_token",
        "tool_use_line",
        "tool_result_line",
        "stream_newline",
        "meta_line",
        "error_bubble",
        "render_event",
        "out",
    ):
        monkeypatch.setattr(Renderer, name, lambda *args, **kwargs: None)


async def test_repl_one_turn_calls_refresh_before_run_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """env 默认(未设)→ refresh 应在 run_turn 前被调一次。"""
    monkeypatch.delenv("CHARIOT_CLI_AUTO_REFRESH", raising=False)
    ctx = _make_ctx(conversation_id=CONV)
    call_order: list[str] = []

    async def _refresh() -> None:
        call_order.append("refresh")

    async def _run_turn(_on_event: Any) -> Any:
        call_order.append("run_turn")
        return _FakeResult()

    monkeypatch.setattr(ctx, "refresh_from_server", _refresh)
    monkeypatch.setattr(ctx, "run_turn", _run_turn)

    await ChatRepl(ctx=ctx)._one_turn("hi")

    assert call_order == ["refresh", "run_turn"]


async def test_repl_one_turn_skips_refresh_when_env_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`CHARIOT_CLI_AUTO_REFRESH=0` → refresh 不被调。"""
    monkeypatch.setenv("CHARIOT_CLI_AUTO_REFRESH", "0")
    ctx = _make_ctx(conversation_id=CONV)
    refresh = AsyncMock()
    monkeypatch.setattr(ctx, "refresh_from_server", refresh)
    monkeypatch.setattr(ctx, "run_turn", AsyncMock(return_value=_FakeResult()))

    await ChatRepl(ctx=ctx)._one_turn("hi")
    refresh.assert_not_awaited()


async def test_repl_one_turn_refresh_runs_for_stateless_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stateless 也调 refresh —— 由 ChatContext.refresh_from_server 内部判 conv_id no-op,
    REPL 不做这层判断,简化分支(refresh 自己处理 stateless)。"""
    monkeypatch.delenv("CHARIOT_CLI_AUTO_REFRESH", raising=False)
    ctx = _make_ctx()  # stateless
    refresh = AsyncMock()
    monkeypatch.setattr(ctx, "refresh_from_server", refresh)
    monkeypatch.setattr(ctx, "run_turn", AsyncMock(return_value=_FakeResult()))

    await ChatRepl(ctx=ctx)._one_turn("hi")
    refresh.assert_awaited_once()


# ============================================================
# helpers
# ============================================================


class _FakeResult:
    text = "ok"
    input_tokens = 1
    output_tokens = 1
    latency_ms = 1

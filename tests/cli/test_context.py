"""ChatContext._build_body 契约测试。

stateful 模式(有 conversation_id)body.messages 必须只含"本轮新增"那条
user msg —— server `Agent._run_tool_loop` 把 body.messages 全部 append
进 messages 表,client 重发已持久化的历史会让 DB 翻倍 + model 看到双份
history。回归值见 `chariot/cli/core/context.py` 模块 docstring 的契约段。
"""

from __future__ import annotations

from unittest.mock import MagicMock

from chariot.cli.core.context import ChatContext


def _make_ctx(*, conversation_id: str | None = None) -> ChatContext:
    return ChatContext(
        client=MagicMock(),
        model="claude-haiku-4-5",
        conversation_id=conversation_id,
    )


# =====================================================================
# stateless:body 全量 messages
# =====================================================================


def test_stateless_build_body_sends_full_history() -> None:
    """无 conversation_id:body.messages = 整段本地历史。"""
    ctx = _make_ctx()
    ctx.append_user("u1")
    ctx.append_assistant("a1")
    ctx.append_user("u2")

    body = ctx._build_body()
    assert body["messages"] == [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
    ]


def test_stateless_build_body_empty_messages() -> None:
    ctx = _make_ctx()
    body = ctx._build_body()
    assert body["messages"] == []


# =====================================================================
# stateful:body 只发末尾一条
# =====================================================================


def test_stateful_build_body_sends_only_last_user_msg() -> None:
    """有 conversation_id:多轮累积本地后,body.messages 只含末尾那条 user。

    防止 client 把已 persist 到 DB 的旧 turn 重发 → server 当作新增重复 append。
    """
    ctx = _make_ctx(conversation_id="01ABCDEF0123456789ABCDEFGH")
    ctx.append_user("u1")
    ctx.append_assistant("a1")
    ctx.append_user("u2")
    ctx.append_assistant("a2")
    ctx.append_user("u3")

    body = ctx._build_body()
    assert body["messages"] == [{"role": "user", "content": "u3"}]


def test_stateful_build_body_first_turn_sends_only_user() -> None:
    """有 conversation_id 且本地刚 reset 后第一轮:body.messages 仅当前 user。"""
    ctx = _make_ctx(conversation_id="01ABCDEF0123456789ABCDEFGH")
    ctx.append_user("hi")

    body = ctx._build_body()
    assert body["messages"] == [{"role": "user", "content": "hi"}]


def test_stateful_build_body_empty_messages() -> None:
    """有 conversation_id 但本地 messages 为空(罕见场景):body.messages 为空。

    实际调用方在发请求前必然 append_user,这里只是契约边界确认。
    """
    ctx = _make_ctx(conversation_id="01ABCDEF0123456789ABCDEFGH")
    body = ctx._build_body()
    assert body["messages"] == []


def test_stateful_does_not_mutate_local_messages() -> None:
    """_build_body 不应修改 self.messages 内容(REPL 后续 pop_last 撤回需要)。"""
    ctx = _make_ctx(conversation_id="01ABCDEF0123456789ABCDEFGH")
    ctx.append_user("u1")
    ctx.append_assistant("a1")
    ctx.append_user("u2")
    snapshot = list(ctx.messages)

    ctx._build_body()
    assert ctx.messages == snapshot


# =====================================================================
# 共通 body 字段
# =====================================================================


def test_build_body_includes_model_and_stream() -> None:
    ctx = _make_ctx()
    ctx.append_user("hi")
    body = ctx._build_body()
    assert body["model"] == "claude-haiku-4-5"
    assert body["stream"] is True
    assert "max_tokens" in body

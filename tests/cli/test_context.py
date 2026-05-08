"""ChatContext 请求构造契约测试(0.6.0 库化版)。

stateful 模式(有 convo_id)req.messages 必须只含"本轮新增"那条
user msg —— AIAgent 内部 load 历史 + persist 这一条。client 重发已持久化
的历史会让 DB 翻倍 + Provider 看到双份 history。详见
`chariot/cli/context.py` 模块 docstring 的契约段。

ChatContext 现在持 AIAgent 实例(不是 ProxyClient);测试用 dummy AIAgent
替身(只需 ChatContext._build_request 不真调 agent.run)。
"""

from __future__ import annotations

from typing import Any

from chariot.cli.context import ChatContext


class _DummyAgent:
    """ChatContext._build_request 不依赖 agent;给个壳骗过 dataclass 字段。"""


def _make_ctx(*, convo_id: str | None = None) -> ChatContext:
    return ChatContext(
        agent=_DummyAgent(),  # type: ignore[arg-type]
        model="claude-haiku-4-5",
        convo_id=convo_id,
    )


# =====================================================================
# stateless:req.messages = 全量本地历史
# =====================================================================


def test_stateless_request_sends_full_history() -> None:
    """无 convo_id:req.messages = 整段本地历史。"""
    ctx = _make_ctx()
    ctx.append_user("u1")
    ctx.append_assistant("a1")
    ctx.append_user("u2")

    req = ctx._build_request()
    assert [(m.role, m.content) for m in req.messages] == [
        ("user", "u1"),
        ("assistant", "a1"),
        ("user", "u2"),
    ]


def test_stateless_request_empty_messages() -> None:
    ctx = _make_ctx()
    req = ctx._build_request()
    assert req.messages == []


# =====================================================================
# stateful:req.messages 只发末尾一条
# =====================================================================


def test_stateful_request_sends_only_last_user_msg() -> None:
    """有 convo_id:多轮累积本地后,req.messages 只含末尾那条 user。

    防止 client 把已 persist 到 DB 的旧 turn 重发 → AIAgent 当作新增重复 append。
    """
    ctx = _make_ctx(convo_id="01ABCDEF0123456789ABCDEFGH")
    ctx.append_user("u1")
    ctx.append_assistant("a1")
    ctx.append_user("u2")
    ctx.append_assistant("a2")
    ctx.append_user("u3")

    req = ctx._build_request()
    assert [(m.role, m.content) for m in req.messages] == [("user", "u3")]


def test_stateful_request_first_turn_sends_only_user() -> None:
    """有 convo_id 且本地刚 reset 后第一轮:req.messages 仅当前 user。"""
    ctx = _make_ctx(convo_id="01ABCDEF0123456789ABCDEFGH")
    ctx.append_user("hi")

    req = ctx._build_request()
    assert [(m.role, m.content) for m in req.messages] == [("user", "hi")]


def test_stateful_request_empty_messages() -> None:
    """有 convo_id 但本地 messages 为空(罕见场景):req.messages 为空。"""
    ctx = _make_ctx(convo_id="01ABCDEF0123456789ABCDEFGH")
    req = ctx._build_request()
    assert req.messages == []


def test_stateful_does_not_mutate_local_messages() -> None:
    """_build_request 不应修改 self.messages 内容(REPL pop_last 撤回需要)。"""
    ctx = _make_ctx(convo_id="01ABCDEF0123456789ABCDEFGH")
    ctx.append_user("u1")
    ctx.append_assistant("a1")
    ctx.append_user("u2")
    snapshot: list[dict[str, Any]] = [dict(m) for m in ctx.messages]

    ctx._build_request()
    assert ctx.messages == snapshot


# =====================================================================
# 共通 ChatRequest 字段
# =====================================================================


def test_request_includes_model_and_max_tokens() -> None:
    ctx = _make_ctx()
    ctx.append_user("hi")
    req = ctx._build_request()
    assert req.model == "claude-haiku-4-5"
    assert req.max_tokens == 1024  # ChatContext 默认值
    # convo_id 透传
    assert req.convo_id is None


def test_request_passes_convo_id_through() -> None:
    """有 convo_id 时,ChatRequest.convo_id 透传(AIAgent 据此走 stateful path)。"""
    ulid = "01ABCDEF0123456789ABCDEFGH"
    ctx = _make_ctx(convo_id=ulid)
    ctx.append_user("hi")
    req = ctx._build_request()
    assert req.convo_id == ulid

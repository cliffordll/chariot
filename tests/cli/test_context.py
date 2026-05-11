"""ChatContext 请求构造契约测试(0.6.0 库化版)。

stateful 模式(有 conversation_id)req.messages 必须只含"本轮新增"那条
user msg —— AIAgent 内部 load 历史 + persist 这一条。client 重发已持久化
的历史会让 DB 翻倍 + Provider 看到双份 history。详见
`chariot/cli/context.py` 模块 docstring 的契约段。

ChatContext 现在持 AIAgent 实例(不是 ProxyClient);测试用 dummy AIAgent
替身(只需 ChatContext._build_request 不真调 agent.run)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chariot.agent.chat_event import ChatEvent
from chariot.cli.context import ChatContext
from chariot.database.session import init_db
from chariot.repos.log_repo import LogRepo


class _DummyAgent:
    """ChatContext._build_request ???? agent;????? dataclass ???"""

    async def run_chat(self, req):  # type: ignore[no-untyped-def]
        yield ChatEvent.message_start(
            message_id="m1",
            model="mock-1",
            usage={"input_tokens": 3},
        )
        yield ChatEvent.message_delta_done(stop_reason="end_turn", usage={"output_tokens": 7})
        yield ChatEvent.message_done()


def _make_ctx(*, conversation_id: str | None = None) -> ChatContext:
    return ChatContext(
        agent=_DummyAgent(),  # type: ignore[arg-type]
        provider_name="claude-haiku-4-5",
        conversation_id=conversation_id,
    )


# =====================================================================
# stateless:req.messages = 全量本地历史
# =====================================================================


def test_stateless_request_sends_full_history() -> None:
    """无 conversation_id:req.messages = 整段本地历史。"""
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
    """有 conversation_id:多轮累积本地后,req.messages 只含末尾那条 user。

    防止 client 把已 persist 到 DB 的旧 turn 重发 → AIAgent 当作新增重复 append。
    """
    ctx = _make_ctx(conversation_id="01ABCDEF0123456789ABCDEFGH")
    ctx.append_user("u1")
    ctx.append_assistant("a1")
    ctx.append_user("u2")
    ctx.append_assistant("a2")
    ctx.append_user("u3")

    req = ctx._build_request()
    assert [(m.role, m.content) for m in req.messages] == [("user", "u3")]


def test_stateful_request_first_turn_sends_only_user() -> None:
    """有 conversation_id 且本地刚 reset 后第一轮:req.messages 仅当前 user。"""
    ctx = _make_ctx(conversation_id="01ABCDEF0123456789ABCDEFGH")
    ctx.append_user("hi")

    req = ctx._build_request()
    assert [(m.role, m.content) for m in req.messages] == [("user", "hi")]


def test_stateful_request_empty_messages() -> None:
    """有 conversation_id 但本地 messages 为空(罕见场景):req.messages 为空。"""
    ctx = _make_ctx(conversation_id="01ABCDEF0123456789ABCDEFGH")
    req = ctx._build_request()
    assert req.messages == []


def test_stateful_does_not_mutate_local_messages() -> None:
    """_build_request 不应修改 self.messages 内容(REPL pop_last 撤回需要)。"""
    ctx = _make_ctx(conversation_id="01ABCDEF0123456789ABCDEFGH")
    ctx.append_user("u1")
    ctx.append_assistant("a1")
    ctx.append_user("u2")
    snapshot: list[dict[str, Any]] = [dict(m) for m in ctx.messages]

    ctx._build_request()
    assert ctx.messages == snapshot


# =====================================================================
# 共通 ChatRequest 字段
# =====================================================================


def test_request_includes_provider_and_max_tokens() -> None:
    ctx = _make_ctx()
    ctx.append_user("hi")
    req = ctx._build_request()
    # ChatRequest.provider_name 是 chariot 路由 key(entry name);wire body.model 由
    # AnthropicProvider 内部从 self.config.model 写
    assert req.provider_name == "claude-haiku-4-5"
    assert req.max_tokens == 1024  # ChatContext 默认值
    # conversation_id 透传
    assert req.conversation_id is None


def test_request_passes_conversation_id_through() -> None:
    """有 conversation_id 时,ChatRequest.conversation_id 透传(AIAgent 据此走 stateful path)。"""
    ulid = "01ABCDEF0123456789ABCDEFGH"
    ctx = _make_ctx(conversation_id=ulid)
    ctx.append_user("hi")
    req = ctx._build_request()
    assert req.conversation_id == ulid


@pytest.mark.asyncio
async def test_run_turn_writes_log_row(tmp_path: Path) -> None:
    db_path = tmp_path / "chariot.db"
    await init_db(db_path)

    ctx = _make_ctx()
    ctx.agent = _DummyAgent()  # type: ignore[assignment]
    ctx.append_user("hi")

    result = await ctx.run_turn(lambda ev: None)
    assert result.input_tokens == 3
    assert result.output_tokens == 7

    sm = await init_db(db_path)
    async with sm() as session:
        logs = await LogRepo(session).list_logs(limit=10, offset=0)

    assert len(logs) == 1
    assert logs[0].provider == "claude-haiku-4-5"
    assert logs[0].status == "ok"
    assert logs[0].input_tokens == 3
    assert logs[0].output_tokens == 7

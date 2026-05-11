"""B3 wave 2 — AIAgent + ContextCompressor 端到端集成。

构造场景:stateful conversation 装满 history,bootstrap 走 'summarizer' aux,
跑一次 run_chat → 验证 trace_turns.meta.context_compressed = True 落库。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chariot.agent.chat_request import ChatRequest, Message
from chariot.agent.registry import AgentRegistry
from chariot.agent.run import AIAgent
from chariot.database.models import TraceTurnRow
from chariot.database.session import dispose_db
from chariot.repos.auxiliary_repo import AuxiliaryRepo
from chariot.repos.conversation_repo import ConversationRepo


@pytest_asyncio.fixture
async def agent(tmp_path: Path) -> AsyncIterator[AIAgent]:
    a = await AIAgent.bootstrap(tmp_path / "test.db")
    AgentRegistry._agents.clear()
    try:
        yield a
    finally:
        AgentRegistry._agents.clear()
        await dispose_db()


async def _seed_long_conversation(sm: async_sessionmaker[AsyncSession], conv_id: str) -> None:
    """灌 history:8 个 user/assistant pair,每条 ~800 char。"""
    big = "x" * 800
    async with sm() as session:
        repo = ConversationRepo(session)
        await repo.create(conv_id)
        for i in range(8):
            await repo.append_message(conv_id, "user", f"u{i} {big}")
            await repo.append_message(conv_id, "assistant", f"a{i} {big}", provider_name="mock")


async def _tighten_summarizer_threshold(sm: async_sessionmaker[AsyncSession], agent: AIAgent) -> None:
    """agent bootstrap 时已用默认 threshold=0.7;为了在 mock + 8K context_length
    下稳定触发,我们换个低 threshold 的 compressor(直接 monkey-patch agent._context_compressor)。"""
    from chariot.context.compressor import ContextCompressor
    from chariot.providers.auxiliary_client import AuxiliaryClient

    async with sm() as session:
        aux_entry = await AuxiliaryRepo(session).get_entry("summarizer")
    assert aux_entry is not None
    provider = agent.providers[aux_entry.provider_entry]
    agent._context_compressor = ContextCompressor(
        AuxiliaryClient(entry=aux_entry, provider=provider),
        threshold=0.1,
        summary_turns=3,
    )


async def test_stateful_run_with_long_history_triggers_compression(
    agent: AIAgent,
) -> None:
    sm = agent.session_maker
    conv_id = "01CONVLONG"
    await _seed_long_conversation(sm, conv_id)
    await _tighten_summarizer_threshold(sm, agent)

    req = ChatRequest(
        provider_name="mock",
        conversation_id=conv_id,
        messages=[Message(role="user", content="please answer briefly")],
    )
    events = [ev async for ev in agent.run_chat(req)]
    assert any(ev.kind == "stream_done" for ev in events)

    # 验证 trace_turns 里最近一条 meta.context_compressed = True
    async with sm() as session:
        stmt = (
            select(TraceTurnRow).where(TraceTurnRow.conversation_id == conv_id).order_by(TraceTurnRow.started_at.desc())
        )
        row = (await session.execute(stmt)).scalars().first()
    assert row is not None
    import json

    meta = json.loads(row.meta)
    assert meta.get("context_compressed") is True
    assert meta["context_compression"]["strategy"] in {"summary", "oldest_pair_pruning"}


async def test_stateful_run_short_history_no_compression(agent: AIAgent) -> None:
    """短对话 → 不触发压缩,trace meta 不含 context_compressed。"""
    sm = agent.session_maker
    conv_id = "01CONVSHORT"
    async with sm() as session:
        await ConversationRepo(session).create(conv_id)
        await ConversationRepo(session).append_message(conv_id, "user", "hi")

    req = ChatRequest(
        provider_name="mock",
        conversation_id=conv_id,
        messages=[Message(role="user", content="hello again")],
    )
    events = [ev async for ev in agent.run_chat(req)]
    assert any(ev.kind == "stream_done" for ev in events)

    async with sm() as session:
        stmt = (
            select(TraceTurnRow).where(TraceTurnRow.conversation_id == conv_id).order_by(TraceTurnRow.started_at.desc())
        )
        row = (await session.execute(stmt)).scalars().first()
    assert row is not None
    import json

    meta = json.loads(row.meta)
    assert meta.get("context_compressed") is None

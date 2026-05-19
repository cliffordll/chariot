"""B3 wave 1 — ConversationRepo FTS5 search / rebuild_fts / extract_indexable_text。"""

from __future__ import annotations

from pathlib import Path

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy import text as sa_text

from chariot.database.models import (
    ContextSnapshotRow,
    ContextTraceRow,
    MemoryLinkRow,
    PromptTraceRow,
    TraceCheckpointRow,
    TraceProviderCallRow,
    TraceToolCallRow,
    TraceTurnRow,
)
from chariot.database.session import dispose_db, init_db
from chariot.repos.conversation_repo import ConversationRepo


@pytest_asyncio.fixture
async def sessionmaker(tmp_path: Path):
    sm = await init_db(tmp_path / "test.db")
    try:
        yield sm
    finally:
        await dispose_db()


def test_extract_indexable_text_from_string() -> None:
    """纯字符串 content 原样返回。"""
    assert ConversationRepo._extract_indexable_text("hello world") == "hello world"


def test_extract_indexable_text_from_blocks() -> None:
    """anthropic blocks:text/tool_use/tool_result 都抽,schema 字段不入。"""
    blocks = [
        {"type": "text", "text": "看一下 README"},
        {"type": "tool_use", "id": "tu1", "name": "read_file", "input": {"path": "README.md"}},
        {"type": "tool_result", "tool_use_id": "tu1", "content": "# Chariot\n..."},
    ]
    result = ConversationRepo._extract_indexable_text(blocks)
    assert "看一下 README" in result
    assert "README.md" in result  # tool_use.input JSON
    assert "# Chariot" in result  # tool_result.content
    # schema 字段不该入
    assert "tool_use_id" not in result
    assert '"name"' not in result


def test_extract_indexable_text_skips_unknown_block_types() -> None:
    blocks = [
        {"type": "text", "text": "kept"},
        {"type": "image", "source": {"url": "x"}},  # 未识别 → 跳
        {"not_a_dict": True},  # 非 dict → 跳
    ]
    assert ConversationRepo._extract_indexable_text(blocks) == "kept"


async def test_search_after_append_finds_hit(sessionmaker) -> None:
    """append_message 同步写 FTS5,search 立刻能查到。"""
    async with sessionmaker() as session:
        repo = ConversationRepo(session)
        await repo.create("01CONVA")
        await repo.append_message("01CONVA", "user", "请帮我看一下 pyproject.toml")
        await repo.append_message("01CONVA", "assistant", "好的", provider_snapshot="mock")
        hits = await repo.search("pyproject")
    assert len(hits) == 1
    assert hits[0].conversation_id == "01CONVA"
    assert hits[0].role == "user"
    # trigram tokenizer 把 <mark> 标签插入查询词中(`pyproje</mark>ct`),所以
    # 不能直接 substring 检 'pyproject';检 mark 出现 + 命中 trigram(`pyproje`)即可
    assert "<mark>" in hits[0].snippet
    assert "pyproje" in hits[0].snippet.lower()


async def test_search_filters_by_conversation_id(sessionmaker) -> None:
    async with sessionmaker() as session:
        repo = ConversationRepo(session)
        await repo.create("01CONVA")
        await repo.create("01CONVB")
        await repo.append_message("01CONVA", "user", "类型注解需要写吗")
        await repo.append_message("01CONVB", "user", "类型注解可以增加可读性")
        all_hits = await repo.search("类型注解")
        a_hits = await repo.search("类型注解", conversation_id="01CONVA")
    assert len(all_hits) == 2
    assert len(a_hits) == 1
    assert a_hits[0].conversation_id == "01CONVA"


async def test_search_respects_limit(sessionmaker) -> None:
    async with sessionmaker() as session:
        repo = ConversationRepo(session)
        await repo.create("01CONV")
        for i in range(5):
            await repo.append_message("01CONV", "user", f"this is message {i} about fts5 search")
        hits = await repo.search("fts5", limit=2)
    assert len(hits) == 2


async def test_search_empty_query_returns_empty(sessionmaker) -> None:
    async with sessionmaker() as session:
        repo = ConversationRepo(session)
        hits = await repo.search("")
        space_hits = await repo.search("   ")
    assert hits == []
    assert space_hits == []


async def test_search_no_hits_returns_empty(sessionmaker) -> None:
    async with sessionmaker() as session:
        repo = ConversationRepo(session)
        await repo.create("01CONV")
        await repo.append_message("01CONV", "user", "hello there")
        hits = await repo.search("nonexistent_keyword_xyz")
    assert hits == []


async def test_search_blocks_content_indexes_text_only(sessionmaker) -> None:
    """append 一个 anthropic blocks 消息,验证只索引 text-only(schema 关键字不该命中)。"""
    async with sessionmaker() as session:
        repo = ConversationRepo(session)
        await repo.create("01CONV")
        await repo.append_message(
            "01CONV",
            "assistant",
            [
                {"type": "text", "text": "hello world"},
                {"type": "tool_use", "id": "tu1", "name": "read_file", "input": {"path": "x"}},
            ],
            provider_snapshot="mock",
        )
        # 真正的内容 "hello" 该命中
        hits_real = await repo.search("hello")
        # schema 字段 "tool_use" 不该命中(只是 type 值,被 _extract 跳过)
        hits_schema = await repo.search("tool_use")
    assert len(hits_real) == 1
    assert hits_schema == []


async def test_delete_conversation_removes_from_fts(sessionmaker) -> None:
    """delete conversation 时 messages_fts 也清。"""
    async with sessionmaker() as session:
        repo = ConversationRepo(session)
        await repo.create("01CONV")
        await repo.append_message("01CONV", "user", "delete me")
        await repo.delete("01CONV")
        hits = await repo.search("delete me")
    assert hits == []


async def test_delete_conversation_cascades_related_records(sessionmaker) -> None:
    async with sessionmaker() as session:
        repo = ConversationRepo(session)
        await repo.create("01CONV")
        await repo.create("01OTHER")
        await repo.append_message("01CONV", "user", "delete me too")
        await repo.append_message("01OTHER", "user", "keep me")

        snapshot = ContextSnapshotRow(
            id="snapshot-conv",
            conversation_id="01CONV",
            provider_snapshot="mock",
        )
        prompt_trace = PromptTraceRow(
            id="prompt-trace-conv",
            bundle_id="bundle-1",
            version_id="version-1",
            conversation_id="01CONV",
            provider_snapshot="mock",
        )
        session.add_all(
            [
                snapshot,
                prompt_trace,
                ContextTraceRow(
                    id="context-trace-conv",
                    snapshot_id="snapshot-conv",
                    conversation_id="01CONV",
                    provider_snapshot="mock",
                    prompt_trace_id="prompt-trace-conv",
                ),
                TraceTurnRow(
                    id="turn-conv",
                    conversation_id="01CONV",
                    provider_snapshot="mock",
                    status="completed",
                ),
                TraceProviderCallRow(
                    id="provider-call-conv",
                    turn_id="turn-conv",
                    provider_snapshot="mock",
                ),
                TraceToolCallRow(
                    id="tool-call-conv",
                    turn_id="turn-conv",
                    tool_name="read_file",
                    status="completed",
                ),
                TraceCheckpointRow(
                    id="checkpoint-conv",
                    turn_id="turn-conv",
                    kind="after_model",
                ),
                MemoryLinkRow(
                    memory_id="memory-conv",
                    link_type="conversation",
                    link_value="01CONV",
                ),
                MemoryLinkRow(
                    memory_id="memory-other-type",
                    link_type="message",
                    link_value="01CONV",
                ),
                TraceTurnRow(
                    id="turn-other",
                    conversation_id="01OTHER",
                    provider_snapshot="mock",
                    status="completed",
                ),
                TraceProviderCallRow(
                    id="provider-call-other",
                    turn_id="turn-other",
                    provider_snapshot="mock",
                ),
                MemoryLinkRow(
                    memory_id="memory-other-conversation",
                    link_type="conversation",
                    link_value="01OTHER",
                ),
            ],
        )
        await session.commit()

        await repo.delete("01CONV")

        assert (
            await session.scalar(
                select(TraceTurnRow.id).where(TraceTurnRow.id == "turn-conv"),
            )
            is None
        )
        assert (
            await session.scalar(
                select(TraceProviderCallRow.id).where(
                    TraceProviderCallRow.id == "provider-call-conv",
                ),
            )
            is None
        )
        assert (
            await session.scalar(
                select(TraceToolCallRow.id).where(TraceToolCallRow.id == "tool-call-conv"),
            )
            is None
        )
        assert (
            await session.scalar(
                select(TraceCheckpointRow.id).where(
                    TraceCheckpointRow.id == "checkpoint-conv",
                ),
            )
            is None
        )
        assert (
            await session.scalar(
                select(PromptTraceRow.id).where(PromptTraceRow.conversation_id == "01CONV"),
            )
            is None
        )
        assert (
            await session.scalar(
                select(ContextSnapshotRow.id).where(
                    ContextSnapshotRow.conversation_id == "01CONV",
                ),
            )
            is None
        )
        assert (
            await session.scalar(
                select(ContextTraceRow.id).where(ContextTraceRow.conversation_id == "01CONV"),
            )
            is None
        )
        assert (
            await session.scalar(
                select(MemoryLinkRow.id).where(
                    MemoryLinkRow.link_type == "conversation",
                    MemoryLinkRow.link_value == "01CONV",
                ),
            )
            is None
        )

        assert (
            await session.scalar(
                select(TraceTurnRow.id).where(TraceTurnRow.id == "turn-other"),
            )
            == "turn-other"
        )
        assert (
            await session.scalar(
                select(TraceProviderCallRow.id).where(
                    TraceProviderCallRow.id == "provider-call-other",
                ),
            )
            == "provider-call-other"
        )
        assert (
            await session.scalar(
                select(MemoryLinkRow.id).where(
                    MemoryLinkRow.link_type == "message",
                    MemoryLinkRow.link_value == "01CONV",
                ),
            )
            is not None
        )
        assert (
            await session.scalar(
                select(MemoryLinkRow.id).where(
                    MemoryLinkRow.link_type == "conversation",
                    MemoryLinkRow.link_value == "01OTHER",
                ),
            )
            is not None
        )


async def test_rebuild_fts_recovers_orphan_state(sessionmaker) -> None:
    """模拟 FTS 跟 messages 不同步:手动清 messages_fts → rebuild_fts 全量回填。"""
    async with sessionmaker() as session:
        repo = ConversationRepo(session)
        await repo.create("01CONV")
        await repo.append_message("01CONV", "user", "find me after rebuild")
        # 制造不同步:手动 DELETE messages_fts 全行
        await session.execute(sa_text("DELETE FROM messages_fts"))
        await session.commit()
        hits_before = await repo.search("rebuild")
        # rebuild
        n = await repo.rebuild_fts()
        hits_after = await repo.search("rebuild")
    assert hits_before == []
    assert n == 1
    assert len(hits_after) == 1

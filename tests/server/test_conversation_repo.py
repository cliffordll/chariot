"""ConversationRepo 测试 —— CRUD + cascade delete + seq 单调 +
load_messages_as_anthropic + last_model 派生 + role 校验。"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.config import ConfigError
from chariot.repos.conversation_repo import ConversationRepo

# 测试用 ULID-ish 形态(repo 不校验形态,但用真实长度的字符串避免误导)
ULID_A = "01JD7K8YQXM2N8R5VF3PCWE4ZB"
ULID_B = "01JD7K8YQXM2N8R5VF3PCWE4ZC"

# ---------- create / get ----------


async def test_create_and_get(session: AsyncSession) -> None:
    repo = ConversationRepo(session)
    conv = await repo.create(ULID_A, title="第一次会话")
    assert conv.id == ULID_A
    assert conv.title == "第一次会话"
    assert conv.last_model is None
    assert conv.message_count == 0

    got = await repo.get(ULID_A)
    assert got is not None
    assert got.id == ULID_A
    assert got.title == "第一次会话"


async def test_create_default_title_is_none(session: AsyncSession) -> None:
    repo = ConversationRepo(session)
    conv = await repo.create(ULID_A)
    assert conv.title is None


async def test_create_duplicate_id_raises(session: AsyncSession) -> None:
    repo = ConversationRepo(session)
    await repo.create(ULID_A)
    with pytest.raises(ConfigError, match="已存在"):
        await repo.create(ULID_A)


async def test_create_empty_id_raises(session: AsyncSession) -> None:
    repo = ConversationRepo(session)
    with pytest.raises(ConfigError, match="非空"):
        await repo.create("")


async def test_get_unknown_returns_none(session: AsyncSession) -> None:
    repo = ConversationRepo(session)
    assert await repo.get(ULID_A) is None


# ---------- ensure_exists(模式 A auto-create)----------


async def test_ensure_exists_creates_when_absent(session: AsyncSession) -> None:
    repo = ConversationRepo(session)
    conv = await repo.ensure_exists(ULID_A)
    assert conv.id == ULID_A
    assert conv.title is None
    assert conv.message_count == 0

    # 确实落盘
    got = await repo.get(ULID_A)
    assert got is not None


async def test_ensure_exists_noop_when_present(session: AsyncSession) -> None:
    repo = ConversationRepo(session)
    await repo.create(ULID_A, title="原标题")
    again = await repo.ensure_exists(ULID_A)
    assert again.title == "原标题"  # 没被覆盖


async def test_ensure_exists_empty_id_raises(session: AsyncSession) -> None:
    repo = ConversationRepo(session)
    with pytest.raises(ConfigError, match="非空"):
        await repo.ensure_exists("")


# ---------- list_entries(updated_at desc 排序)----------


async def test_list_entries_sorted_by_updated_desc(session: AsyncSession) -> None:
    import asyncio

    repo = ConversationRepo(session)
    await repo.create(ULID_A, title="A")
    await asyncio.sleep(0.01)
    await repo.create(ULID_B, title="B")
    # 给 A 加一条 message,触发 updated_at 撞新(A 应排到 B 前)
    await asyncio.sleep(0.01)
    await repo.append_message(ULID_A, "user", "hi")

    entries = await repo.list_entries()
    assert [e.id for e in entries] == [ULID_A, ULID_B]


async def test_list_entries_message_count(session: AsyncSession) -> None:
    repo = ConversationRepo(session)
    await repo.create(ULID_A)
    await repo.append_message(ULID_A, "user", "hi")
    await repo.append_message(ULID_A, "assistant", "hello", model_name="mock")
    await repo.append_message(ULID_A, "user", "继续")

    entries = await repo.list_entries()
    assert len(entries) == 1
    assert entries[0].message_count == 3


async def test_list_entries_limit_offset(session: AsyncSession) -> None:
    repo = ConversationRepo(session)
    for i in range(5):
        await repo.create(f"01JD000000000000000000000{i}")

    page1 = await repo.list_entries(limit=2, offset=0)
    page2 = await repo.list_entries(limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 2
    assert {c.id for c in page1}.isdisjoint({c.id for c in page2})


# ---------- update_title ----------


async def test_update_title(session: AsyncSession) -> None:
    repo = ConversationRepo(session)
    await repo.create(ULID_A, title="旧标题")
    updated = await repo.update_title(ULID_A, "新标题")
    assert updated.title == "新标题"


async def test_update_title_to_none(session: AsyncSession) -> None:
    repo = ConversationRepo(session)
    await repo.create(ULID_A, title="旧标题")
    updated = await repo.update_title(ULID_A, None)
    assert updated.title is None


async def test_update_title_unknown_raises(session: AsyncSession) -> None:
    repo = ConversationRepo(session)
    with pytest.raises(ConfigError, match="未知"):
        await repo.update_title(ULID_A, "x")


# ---------- delete + cascade ----------


async def test_delete_removes_conversation(session: AsyncSession) -> None:
    repo = ConversationRepo(session)
    await repo.create(ULID_A)
    await repo.delete(ULID_A)
    assert await repo.get(ULID_A) is None


async def test_delete_cascade_removes_messages(session: AsyncSession) -> None:
    repo = ConversationRepo(session)
    await repo.create(ULID_A)
    await repo.append_message(ULID_A, "user", "hi")
    await repo.append_message(ULID_A, "assistant", "hello", model_name="mock")
    await repo.delete(ULID_A)

    # 直接查 messages 表确认 cascade 干净
    from sqlalchemy import select

    from chariot.database.models import MessageRow

    rows = (
        (
            await session.execute(
                select(MessageRow).where(MessageRow.conversation_id == ULID_A),
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 0


async def test_delete_unknown_raises(session: AsyncSession) -> None:
    repo = ConversationRepo(session)
    with pytest.raises(ConfigError, match="未知"):
        await repo.delete(ULID_A)


# ---------- append_message ----------


async def test_append_message_seq_starts_at_zero(session: AsyncSession) -> None:
    repo = ConversationRepo(session)
    await repo.create(ULID_A)
    msg = await repo.append_message(ULID_A, "user", "first")
    assert msg.seq == 0


async def test_append_message_seq_monotonic(session: AsyncSession) -> None:
    repo = ConversationRepo(session)
    await repo.create(ULID_A)
    m1 = await repo.append_message(ULID_A, "user", "1")
    m2 = await repo.append_message(ULID_A, "assistant", "2", model_name="mock")
    m3 = await repo.append_message(ULID_A, "user", "3")
    assert (m1.seq, m2.seq, m3.seq) == (0, 1, 2)


async def test_append_message_seq_isolated_per_conversation(
    session: AsyncSession,
) -> None:
    """两个 conv 各自 seq 从 0 起,互不串。"""
    repo = ConversationRepo(session)
    await repo.create(ULID_A)
    await repo.create(ULID_B)
    a1 = await repo.append_message(ULID_A, "user", "a1")
    b1 = await repo.append_message(ULID_B, "user", "b1")
    a2 = await repo.append_message(ULID_A, "user", "a2")
    assert a1.seq == 0
    assert a2.seq == 1
    assert b1.seq == 0


async def test_append_message_invalid_role_raises(session: AsyncSession) -> None:
    repo = ConversationRepo(session)
    await repo.create(ULID_A)
    with pytest.raises(ConfigError, match="role"):
        await repo.append_message(ULID_A, "tool_use", "x")


async def test_append_message_model_name_only_assistant(
    session: AsyncSession,
) -> None:
    """model_name 只能跟 role='assistant' 一起填。"""
    repo = ConversationRepo(session)
    await repo.create(ULID_A)
    with pytest.raises(ConfigError, match="model_name"):
        await repo.append_message(ULID_A, "user", "hi", model_name="mock")


async def test_append_assistant_updates_last_model(session: AsyncSession) -> None:
    repo = ConversationRepo(session)
    await repo.create(ULID_A)
    await repo.append_message(ULID_A, "user", "hi")
    got = await repo.get(ULID_A)
    assert got is not None
    assert got.last_model is None  # user 行不动 last_model

    await repo.append_message(ULID_A, "assistant", "hello", model_name="mock")
    got = await repo.get(ULID_A)
    assert got is not None
    assert got.last_model == "mock"

    # 切到另一个 entry,last_model 跟着变
    await repo.append_message(ULID_A, "user", "再问")
    await repo.append_message(ULID_A, "assistant", "答", model_name="claude")
    got = await repo.get(ULID_A)
    assert got is not None
    assert got.last_model == "claude"


async def test_append_message_string_content(session: AsyncSession) -> None:
    """content 是字符串(纯文本 message),原样持久化。"""
    repo = ConversationRepo(session)
    await repo.create(ULID_A)
    await repo.append_message(ULID_A, "user", "hello world")

    msgs = await repo.load_messages_as_anthropic(ULID_A)
    assert msgs == [{"role": "user", "content": "hello world"}]


async def test_append_message_blocks_content(session: AsyncSession) -> None:
    """content 是 anthropic blocks 数组(含 tool_use / tool_result),原样持久化。"""
    repo = ConversationRepo(session)
    await repo.create(ULID_A)

    assistant_blocks: list[dict[str, object]] = [
        {"type": "text", "text": "我读一下"},
        {
            "type": "tool_use",
            "id": "tu_1",
            "name": "read_file",
            "input": {"path": "x.txt"},
        },
    ]
    await repo.append_message(
        ULID_A,
        "assistant",
        assistant_blocks,
        model_name="mock",
    )
    user_blocks: list[dict[str, object]] = [
        {"type": "tool_result", "tool_use_id": "tu_1", "content": "文件内容"},
    ]
    await repo.append_message(ULID_A, "user", user_blocks)

    msgs = await repo.load_messages_as_anthropic(ULID_A)
    assert msgs == [
        {"role": "assistant", "content": assistant_blocks},
        {"role": "user", "content": user_blocks},
    ]


# ---------- load_messages_as_anthropic / list_messages ----------


async def test_load_messages_empty_conversation(session: AsyncSession) -> None:
    repo = ConversationRepo(session)
    await repo.create(ULID_A)
    msgs = await repo.load_messages_as_anthropic(ULID_A)
    assert msgs == []


async def test_load_messages_seq_order(session: AsyncSession) -> None:
    """load 出来的顺序按 seq 升序,即写入顺序。"""
    repo = ConversationRepo(session)
    await repo.create(ULID_A)
    await repo.append_message(ULID_A, "user", "1")
    await repo.append_message(ULID_A, "assistant", "2", model_name="mock")
    await repo.append_message(ULID_A, "user", "3")

    msgs = await repo.load_messages_as_anthropic(ULID_A)
    assert [m["content"] for m in msgs] == ["1", "2", "3"]


async def test_list_messages_returns_orm_rows(session: AsyncSession) -> None:
    """list_messages 返原始 ORM rows(给详情端点拿 seq / model_name / created_at)。"""
    repo = ConversationRepo(session)
    await repo.create(ULID_A)
    await repo.append_message(ULID_A, "user", "hi")
    await repo.append_message(ULID_A, "assistant", "hello", model_name="mock")

    rows = await repo.list_messages(ULID_A)
    assert len(rows) == 2
    assert rows[0].seq == 0
    assert rows[0].role == "user"
    assert rows[0].model_name is None
    assert rows[1].seq == 1
    assert rows[1].role == "assistant"
    assert rows[1].model_name == "mock"
    assert all(r.created_at is not None for r in rows)


# ---------- updated_at 触发 ----------


async def test_updated_at_advances_on_append(session: AsyncSession) -> None:
    """每次 append 都撞 conversations.updated_at(让 GUI 列表按"最近活跃"排序)。"""
    import asyncio

    repo = ConversationRepo(session)
    await repo.create(ULID_A)
    before = (await repo.get(ULID_A)).updated_at  # type: ignore[union-attr]
    await asyncio.sleep(0.01)
    await repo.append_message(ULID_A, "user", "hi")
    after = (await repo.get(ULID_A)).updated_at  # type: ignore[union-attr]
    assert after > before

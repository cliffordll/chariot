"""ToolRepo 测试 —— seed_if_empty(4 条 fixture) + list_enabled + update 路径。"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.config import ConfigError, ToolNotFound
from chariot.repos.tool_repo import ToolRepo

# ---------- seed_if_empty ----------


async def test_seed_if_empty_writes_four_disabled_fixtures(
    session: AsyncSession,
) -> None:
    """空 DB → 写 4 条 fixture,全部 enabled=False,默认 options 落盘。"""
    repo = ToolRepo(session)
    await repo.seed_if_empty()

    entries = await repo.list_entries()
    names = [e.name for e in entries]
    assert sorted(names) == ["http_get", "list_dir", "read_file", "shell_exec"]
    assert all(not e.enabled for e in entries), "全部默认 disabled"

    by_name = {e.name: e for e in entries}
    assert by_name["read_file"].options == {"max_bytes": 1048576}
    assert by_name["list_dir"].options == {}
    assert by_name["shell_exec"].options == {
        "workdir": "~/.chariot/sandbox",
        "timeout_s": 30,
    }
    assert by_name["http_get"].options == {"allowed_domains": [], "max_bytes": 524288}


async def test_seed_if_empty_no_op_when_table_has_rows(
    session: AsyncSession,
) -> None:
    """表非空时 seed 不动数据。"""
    repo = ToolRepo(session)
    await repo.seed_if_empty()
    await repo.update("read_file", enabled=True)  # 标记一下,看下次 seed 会不会被覆盖

    await repo.seed_if_empty()  # 再调一次

    got = await repo.get_entry("read_file")
    assert got is not None
    assert got.enabled is True  # 仍然 enabled,seed 没覆盖


# ---------- list_entries / list_enabled ----------


async def test_list_enabled_filters_disabled(session: AsyncSession) -> None:
    repo = ToolRepo(session)
    await repo.seed_if_empty()
    await repo.update("read_file", enabled=True)
    await repo.update("list_dir", enabled=True)

    enabled = await repo.list_enabled()
    names = sorted(e.name for e in enabled)
    assert names == ["list_dir", "read_file"]
    assert all(e.enabled for e in enabled)


async def test_list_entries_includes_disabled(session: AsyncSession) -> None:
    repo = ToolRepo(session)
    await repo.seed_if_empty()
    entries = await repo.list_entries()
    assert len(entries) == 4


# ---------- get_entry ----------


async def test_get_entry_by_name(session: AsyncSession) -> None:
    repo = ToolRepo(session)
    await repo.seed_if_empty()
    got = await repo.get_entry("shell_exec")
    assert got is not None
    assert got.name == "shell_exec"
    assert got.type == "shell_exec"
    assert got.enabled is False


async def test_get_entry_unknown_returns_none(session: AsyncSession) -> None:
    repo = ToolRepo(session)
    await repo.seed_if_empty()
    assert await repo.get_entry("ghost_tool") is None


# ---------- update ----------


async def test_update_toggles_enabled(session: AsyncSession) -> None:
    repo = ToolRepo(session)
    await repo.seed_if_empty()
    updated = await repo.update("http_get", enabled=True)
    assert updated.enabled is True

    updated2 = await repo.update("http_get", enabled=False)
    assert updated2.enabled is False


async def test_update_options_only(session: AsyncSession) -> None:
    repo = ToolRepo(session)
    await repo.seed_if_empty()
    updated = await repo.update(
        "http_get",
        options={"allowed_domains": ["api.github.com"], "max_bytes": 1024},
    )
    assert updated.options == {
        "allowed_domains": ["api.github.com"],
        "max_bytes": 1024,
    }
    assert updated.enabled is False  # 未改


async def test_update_both_fields(session: AsyncSession) -> None:
    repo = ToolRepo(session)
    await repo.seed_if_empty()
    updated = await repo.update(
        "shell_exec",
        enabled=True,
        options={"workdir": "/tmp/sandbox", "timeout_s": 60},
    )
    assert updated.enabled is True
    assert updated.options == {"workdir": "/tmp/sandbox", "timeout_s": 60}


async def test_update_unknown_raises_tool_not_found(session: AsyncSession) -> None:
    repo = ToolRepo(session)
    await repo.seed_if_empty()
    with pytest.raises(ToolNotFound, match="未知"):
        await repo.update("ghost_tool", enabled=True)


async def test_update_options_not_serializable_raises(session: AsyncSession) -> None:
    repo = ToolRepo(session)
    await repo.seed_if_empty()
    with pytest.raises(ConfigError, match="序列化"):
        await repo.update("read_file", options={"bad": {1, 2, 3}})  # type: ignore[dict-item]


# ---------- ORM 字段(timestamps)----------


async def test_seed_sets_timestamps(session: AsyncSession) -> None:
    """seed 写的行 created_at / updated_at 非空(对应 SQL DEFAULT CURRENT_TIMESTAMP
    + ORM `default=_utcnow`)。"""
    from sqlalchemy import select

    from chariot.database.models import ToolRow

    repo = ToolRepo(session)
    await repo.seed_if_empty()

    rows = (await session.execute(select(ToolRow))).scalars().all()
    assert all(r.created_at is not None for r in rows)
    assert all(r.updated_at is not None for r in rows)


async def test_seed_options_persisted_as_json(session: AsyncSession) -> None:
    """options 列在 DB 里是合法 JSON 字符串(确认 _serialize_json 路径)。"""
    from sqlalchemy import select

    from chariot.database.models import ToolRow

    repo = ToolRepo(session)
    await repo.seed_if_empty()

    row = (await session.execute(select(ToolRow).where(ToolRow.name == "read_file"))).scalar_one()
    assert json.loads(row.options) == {"max_bytes": 1048576}

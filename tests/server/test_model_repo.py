"""ModelRepo 测试 —— CRUD + duplicate 命名规则 + seed_if_empty + 校验路径。"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.server.config import ConfigError
from chariot.server.database.models import ModelRow, SettingRow
from chariot.server.repository.model_repo import ModelRepo

# ---------- create / get / list ----------


async def test_create_and_get_entry(session: AsyncSession) -> None:
    repo = ModelRepo(session)
    entry = await repo.create(
        name="claude",
        type="anthropic",
        options={"model": "claude-opus-4-5", "api_key": "sk-x"},
    )
    assert entry.name == "claude"
    assert entry.type == "anthropic"
    assert entry.options == {"model": "claude-opus-4-5", "api_key": "sk-x"}

    got = await repo.get_entry("claude")
    assert got is not None
    assert got == entry


async def test_get_entry_unknown_returns_none(session: AsyncSession) -> None:
    repo = ModelRepo(session)
    assert await repo.get_entry("ghost") is None


async def test_list_entries_returns_all_in_creation_order(
    session: AsyncSession,
) -> None:
    repo = ModelRepo(session)
    await repo.create(name="a", type="mock", options={})
    await repo.create(name="b", type="mock", options={})
    await repo.create(name="c", type="mock", options={})
    names = [e.name for e in await repo.list_entries()]
    assert names == ["a", "b", "c"]


async def test_create_duplicate_name_raises(session: AsyncSession) -> None:
    repo = ModelRepo(session)
    await repo.create(name="x", type="mock", options={})
    with pytest.raises(ConfigError, match="已存在"):
        await repo.create(name="x", type="mock", options={})


async def test_create_empty_name_raises(session: AsyncSession) -> None:
    repo = ModelRepo(session)
    with pytest.raises(ConfigError, match="name"):
        await repo.create(name="", type="mock", options={})


async def test_create_empty_type_raises(session: AsyncSession) -> None:
    repo = ModelRepo(session)
    with pytest.raises(ConfigError, match="type"):
        await repo.create(name="x", type="", options={})


async def test_create_options_not_serializable_raises(session: AsyncSession) -> None:
    repo = ModelRepo(session)
    with pytest.raises(ConfigError, match="序列化"):
        # set 不可 JSON 序列化
        await repo.create(name="x", type="mock", options={"bad": {1, 2, 3}})  # type: ignore[dict-item]


# ---------- update ----------


async def test_update_changes_type_and_options(session: AsyncSession) -> None:
    repo = ModelRepo(session)
    await repo.create(name="x", type="mock", options={"a": 1})
    updated = await repo.update(
        "x",
        type="anthropic",
        options={"model": "claude-haiku-4-5"},
    )
    assert updated.type == "anthropic"
    assert updated.options == {"model": "claude-haiku-4-5"}


async def test_update_only_options(session: AsyncSession) -> None:
    repo = ModelRepo(session)
    await repo.create(name="x", type="mock", options={"a": 1})
    updated = await repo.update("x", options={"a": 2})
    assert updated.type == "mock"  # 不变
    assert updated.options == {"a": 2}


async def test_update_unknown_name_raises(session: AsyncSession) -> None:
    repo = ModelRepo(session)
    with pytest.raises(ConfigError, match="未知"):
        await repo.update("ghost", options={"x": 1})


# ---------- delete ----------


async def test_delete_removes_entry(session: AsyncSession) -> None:
    repo = ModelRepo(session)
    await repo.create(name="x", type="mock", options={})
    await repo.delete("x")
    assert await repo.get_entry("x") is None


async def test_delete_unknown_raises(session: AsyncSession) -> None:
    repo = ModelRepo(session)
    with pytest.raises(ConfigError, match="未知"):
        await repo.delete("ghost")


# ---------- duplicate ----------


async def test_duplicate_default_name_is_src_copy(session: AsyncSession) -> None:
    repo = ModelRepo(session)
    await repo.create(name="src", type="mock", options={"k": "v"})
    dup = await repo.duplicate("src")
    assert dup.name == "src_copy"
    assert dup.options == {"k": "v"}
    assert dup.type == "mock"


async def test_duplicate_collision_increments_suffix(session: AsyncSession) -> None:
    repo = ModelRepo(session)
    await repo.create(name="src", type="mock", options={})
    await repo.duplicate("src")  # → src_copy
    await repo.duplicate("src")  # → src_copy_2
    third = await repo.duplicate("src")  # → src_copy_3
    assert third.name == "src_copy_3"


async def test_duplicate_explicit_as_name(session: AsyncSession) -> None:
    repo = ModelRepo(session)
    await repo.create(name="src", type="anthropic", options={"model": "x"})
    dup = await repo.duplicate("src", as_name="my-custom")
    assert dup.name == "my-custom"
    assert dup.options == {"model": "x"}


async def test_duplicate_explicit_name_collision_raises(session: AsyncSession) -> None:
    repo = ModelRepo(session)
    await repo.create(name="src", type="mock", options={})
    await repo.create(name="taken", type="mock", options={})
    with pytest.raises(ConfigError, match="已存在"):
        await repo.duplicate("src", as_name="taken")


async def test_duplicate_unknown_src_raises(session: AsyncSession) -> None:
    repo = ModelRepo(session)
    with pytest.raises(ConfigError, match="未知"):
        await repo.duplicate("ghost")


# ---------- active / settings ----------


async def test_get_active_when_unset_returns_none(session: AsyncSession) -> None:
    repo = ModelRepo(session)
    assert await repo.get_active() is None


async def test_set_active_persists_and_get_returns(session: AsyncSession) -> None:
    repo = ModelRepo(session)
    await repo.create(name="m", type="mock", options={})
    await repo.set_active("m")
    assert await repo.get_active() == "m"


async def test_set_active_unknown_name_raises(session: AsyncSession) -> None:
    repo = ModelRepo(session)
    with pytest.raises(ConfigError, match="未知"):
        await repo.set_active("ghost")


async def test_set_active_overwrites_previous(session: AsyncSession) -> None:
    repo = ModelRepo(session)
    await repo.create(name="a", type="mock", options={})
    await repo.create(name="b", type="mock", options={})
    await repo.set_active("a")
    await repo.set_active("b")
    assert await repo.get_active() == "b"


async def test_clear_active_unsets(session: AsyncSession) -> None:
    repo = ModelRepo(session)
    await repo.create(name="m", type="mock", options={})
    await repo.set_active("m")
    await repo.clear_active()
    assert await repo.get_active() is None


# ---------- seed_if_empty ----------


async def test_seed_if_empty_seeds_mock(session: AsyncSession) -> None:
    """空 DB → seed mock entry + active=mock。"""
    repo = ModelRepo(session)
    await repo.seed_if_empty()

    rows = await repo.list_rows()
    assert len(rows) == 1
    assert rows[0].name == "mock"
    assert rows[0].type == "mock"
    assert json.loads(rows[0].options) == {}

    assert await repo.get_active() == "mock"


async def test_seed_if_empty_no_op_when_table_has_rows(session: AsyncSession) -> None:
    """表非空时 seed 完全不动数据(不插 mock,不改 active)。"""
    repo = ModelRepo(session)
    await repo.create(name="my-claude", type="mock", options={})
    await repo.set_active("my-claude")

    await repo.seed_if_empty()

    rows = await repo.list_rows()
    assert len(rows) == 1
    assert rows[0].name == "my-claude"
    assert await repo.get_active() == "my-claude"


# ---------- ORM 直接验(确认 created_at / updated_at)----------


async def test_create_sets_timestamps(session: AsyncSession) -> None:
    repo = ModelRepo(session)
    await repo.create(name="x", type="mock", options={})
    rows = await repo.list_rows()
    assert rows[0].created_at is not None
    assert rows[0].updated_at is not None


async def test_settings_row_isolation_from_models(session: AsyncSession) -> None:
    """sanity:settings 表和 models 表是两张独立表,互不影响。"""
    repo = ModelRepo(session)
    await repo.create(name="m", type="mock", options={})
    await repo.set_active("m")
    # 直接 ORM 查 settings 也能看到一行
    s_row = await session.get(SettingRow, "active_model")
    assert s_row is not None and s_row.value == "m"
    # models 表只有一行
    rows = await repo.list_rows()
    assert isinstance(rows[0], ModelRow)

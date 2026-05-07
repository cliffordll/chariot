"""ModelRepo 测试 —— CRUD + duplicate 命名规则 + seed_if_empty + 校验路径。"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.config import ConfigError
from chariot.repos.model_repo import ModelRepo

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
    assert entry.params == {}  # 默认空 dict

    got = await repo.get_entry("claude")
    assert got is not None
    assert got == entry


async def test_create_with_params(session: AsyncSession) -> None:
    repo = ModelRepo(session)
    entry = await repo.create(
        name="claude",
        type="anthropic",
        options={"model": "claude-opus-4-5"},
        params={"temperature": 0.7, "max_tokens": 2048},
    )
    assert entry.params == {"temperature": 0.7, "max_tokens": 2048}


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


async def test_create_params_not_serializable_raises(session: AsyncSession) -> None:
    repo = ModelRepo(session)
    with pytest.raises(ConfigError, match="序列化"):
        await repo.create(
            name="x",
            type="mock",
            options={},
            params={"bad": {1, 2, 3}},  # type: ignore[dict-item]
        )


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


async def test_update_only_params(session: AsyncSession) -> None:
    """改 params 不动 options / type。"""
    repo = ModelRepo(session)
    await repo.create(
        name="x", type="anthropic", options={"model": "y"}, params={"temperature": 0.5}
    )
    updated = await repo.update("x", params={"temperature": 1.0, "top_p": 0.95})
    assert updated.options == {"model": "y"}
    assert updated.type == "anthropic"
    assert updated.params == {"temperature": 1.0, "top_p": 0.95}


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
    await repo.create(name="src", type="mock", options={"k": "v"}, params={"t": 0.5})
    dup = await repo.duplicate("src")
    assert dup.name == "src_copy"
    assert dup.options == {"k": "v"}
    assert dup.params == {"t": 0.5}  # params 也应当复制
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


# ---------- seed_if_empty ----------


async def test_seed_if_empty_seeds_mock(session: AsyncSession) -> None:
    """空 DB → seed mock entry(0.3.1 起不再写 active)。"""
    repo = ModelRepo(session)
    await repo.seed_if_empty()

    rows = await repo.list_rows()
    assert len(rows) == 1
    assert rows[0].name == "mock"
    assert rows[0].type == "mock"
    assert json.loads(rows[0].options) == {}
    assert json.loads(rows[0].params) == {}


async def test_seed_if_empty_no_op_when_table_has_rows(session: AsyncSession) -> None:
    """表非空时 seed 完全不动数据。"""
    repo = ModelRepo(session)
    await repo.create(name="my-claude", type="mock", options={})

    await repo.seed_if_empty()

    rows = await repo.list_rows()
    assert len(rows) == 1
    assert rows[0].name == "my-claude"


# ---------- ORM 直接验(确认 created_at / updated_at)----------


async def test_create_sets_timestamps(session: AsyncSession) -> None:
    repo = ModelRepo(session)
    await repo.create(name="x", type="mock", options={})
    rows = await repo.list_rows()
    assert rows[0].created_at is not None
    assert rows[0].updated_at is not None

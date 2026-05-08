"""ProviderRepo 测试 —— CRUD + duplicate 命名规则 + seed_if_empty + 校验路径 + 默认 provider。"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.config import ConfigError
from chariot.agent.exceptions import ProviderNotFound
from chariot.repos.provider_repo import ProviderRepo

# ---------- create / get / list ----------


async def test_create_and_get_entry(session: AsyncSession) -> None:
    repo = ProviderRepo(session)
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
    repo = ProviderRepo(session)
    entry = await repo.create(
        name="claude",
        type="anthropic",
        options={"model": "claude-opus-4-5"},
        params={"temperature": 0.7, "max_tokens": 2048},
    )
    assert entry.params == {"temperature": 0.7, "max_tokens": 2048}


async def test_get_entry_unknown_returns_none(session: AsyncSession) -> None:
    repo = ProviderRepo(session)
    assert await repo.get_entry("ghost") is None


async def test_list_entries_returns_all_in_creation_order(
    session: AsyncSession,
) -> None:
    repo = ProviderRepo(session)
    await repo.create(name="a", type="mock", options={})
    await repo.create(name="b", type="mock", options={})
    await repo.create(name="c", type="mock", options={})
    names = [e.name for e in await repo.list_entries()]
    assert names == ["a", "b", "c"]


async def test_create_duplicate_name_raises(session: AsyncSession) -> None:
    repo = ProviderRepo(session)
    await repo.create(name="x", type="mock", options={})
    with pytest.raises(ConfigError, match="已存在"):
        await repo.create(name="x", type="mock", options={})


async def test_create_empty_name_raises(session: AsyncSession) -> None:
    repo = ProviderRepo(session)
    with pytest.raises(ConfigError, match="name"):
        await repo.create(name="", type="mock", options={})


async def test_create_empty_type_raises(session: AsyncSession) -> None:
    repo = ProviderRepo(session)
    with pytest.raises(ConfigError, match="type"):
        await repo.create(name="x", type="", options={})


async def test_create_options_not_serializable_raises(session: AsyncSession) -> None:
    repo = ProviderRepo(session)
    with pytest.raises(ConfigError, match="序列化"):
        # set 不可 JSON 序列化
        await repo.create(name="x", type="mock", options={"bad": {1, 2, 3}})  # type: ignore[dict-item]


async def test_create_params_not_serializable_raises(session: AsyncSession) -> None:
    repo = ProviderRepo(session)
    with pytest.raises(ConfigError, match="序列化"):
        await repo.create(
            name="x",
            type="mock",
            options={},
            params={"bad": {1, 2, 3}},  # type: ignore[dict-item]
        )


# ---------- update ----------


async def test_update_changes_type_and_options(session: AsyncSession) -> None:
    repo = ProviderRepo(session)
    await repo.create(name="x", type="mock", options={"a": 1})
    updated = await repo.update(
        "x",
        type="anthropic",
        options={"model": "claude-haiku-4-5"},
    )
    assert updated.type == "anthropic"
    assert updated.options == {"model": "claude-haiku-4-5"}


async def test_update_only_options(session: AsyncSession) -> None:
    repo = ProviderRepo(session)
    await repo.create(name="x", type="mock", options={"a": 1})
    updated = await repo.update("x", options={"a": 2})
    assert updated.type == "mock"  # 不变
    assert updated.options == {"a": 2}


async def test_update_only_params(session: AsyncSession) -> None:
    """改 params 不动 options / type。"""
    repo = ProviderRepo(session)
    await repo.create(
        name="x", type="anthropic", options={"model": "y"}, params={"temperature": 0.5}
    )
    updated = await repo.update("x", params={"temperature": 1.0, "top_p": 0.95})
    assert updated.options == {"model": "y"}
    assert updated.type == "anthropic"
    assert updated.params == {"temperature": 1.0, "top_p": 0.95}


async def test_update_unknown_name_raises(session: AsyncSession) -> None:
    repo = ProviderRepo(session)
    with pytest.raises(ConfigError, match="未知"):
        await repo.update("ghost", options={"x": 1})


# ---------- delete ----------


async def test_delete_removes_entry(session: AsyncSession) -> None:
    repo = ProviderRepo(session)
    await repo.create(name="x", type="mock", options={})
    await repo.delete("x")
    assert await repo.get_entry("x") is None


async def test_delete_unknown_raises(session: AsyncSession) -> None:
    repo = ProviderRepo(session)
    with pytest.raises(ConfigError, match="未知"):
        await repo.delete("ghost")


# ---------- duplicate ----------


async def test_duplicate_default_name_is_src_copy(session: AsyncSession) -> None:
    repo = ProviderRepo(session)
    await repo.create(name="src", type="mock", options={"k": "v"}, params={"t": 0.5})
    dup = await repo.copy("src")
    assert dup.name == "src_copy"
    assert dup.options == {"k": "v"}
    assert dup.params == {"t": 0.5}  # params 也应当复制
    assert dup.type == "mock"


async def test_duplicate_collision_increments_suffix(session: AsyncSession) -> None:
    repo = ProviderRepo(session)
    await repo.create(name="src", type="mock", options={})
    await repo.copy("src")  # → src_copy
    await repo.copy("src")  # → src_copy_2
    third = await repo.copy("src")  # → src_copy_3
    assert third.name == "src_copy_3"


async def test_duplicate_explicit_as_name(session: AsyncSession) -> None:
    repo = ProviderRepo(session)
    await repo.create(name="src", type="anthropic", options={"model": "x"})
    dup = await repo.copy("src", as_name="my-custom")
    assert dup.name == "my-custom"
    assert dup.options == {"model": "x"}


async def test_duplicate_explicit_name_collision_raises(session: AsyncSession) -> None:
    repo = ProviderRepo(session)
    await repo.create(name="src", type="mock", options={})
    await repo.create(name="taken", type="mock", options={})
    with pytest.raises(ConfigError, match="已存在"):
        await repo.copy("src", as_name="taken")


async def test_duplicate_unknown_src_raises(session: AsyncSession) -> None:
    repo = ProviderRepo(session)
    with pytest.raises(ConfigError, match="未知"):
        await repo.copy("ghost")


# ---------- seed_if_empty ----------


async def test_seed_if_empty_seeds_mock(session: AsyncSession) -> None:
    """空 DB → seed mock entry(0.3.1 起不再写 active)。"""
    repo = ProviderRepo(session)
    await repo.seed_if_empty()

    rows = await repo.list_rows()
    assert len(rows) == 1
    assert rows[0].name == "mock"
    assert rows[0].type == "mock"
    assert json.loads(rows[0].options) == {}
    assert json.loads(rows[0].params) == {}


async def test_seed_if_empty_no_op_when_table_has_rows(session: AsyncSession) -> None:
    """表非空时 seed 完全不动数据。"""
    repo = ProviderRepo(session)
    await repo.create(name="my-claude", type="mock", options={})

    await repo.seed_if_empty()

    rows = await repo.list_rows()
    assert len(rows) == 1
    assert rows[0].name == "my-claude"


# ---------- ORM 直接验(确认 created_at / updated_at)----------


async def test_create_sets_timestamps(session: AsyncSession) -> None:
    repo = ProviderRepo(session)
    await repo.create(name="x", type="mock", options={})
    rows = await repo.list_rows()
    assert rows[0].created_at is not None
    assert rows[0].updated_at is not None


# ---------- 默认 provider(v7 起;is_default 列)----------


async def test_get_default_returns_none_on_empty_db(session: AsyncSession) -> None:
    """空表 → get_default 返 None。"""
    repo = ProviderRepo(session)
    assert await repo.get_default() is None


async def test_get_default_returns_none_when_no_row_marked(session: AsyncSession) -> None:
    """表里有行但没 is_default=1 → 返 None(初始无默认是合法状态)。"""
    repo = ProviderRepo(session)
    await repo.create(name="a", type="mock", options={})
    await repo.create(name="b", type="mock", options={})
    assert await repo.get_default() is None


async def test_set_default_then_get_default_returns_selected(session: AsyncSession) -> None:
    repo = ProviderRepo(session)
    await repo.create(name="a", type="mock", options={})
    await repo.create(name="b", type="mock", options={})
    await repo.set_default("b")

    default = await repo.get_default()
    assert default is not None
    assert default.name == "b"


async def test_set_default_switches_atomically(session: AsyncSession) -> None:
    """连续 set_default(a) → set_default(b),只有 b 是默认(a 自动清 0)。"""
    repo = ProviderRepo(session)
    await repo.create(name="a", type="mock", options={})
    await repo.create(name="b", type="mock", options={})
    await repo.set_default("a")
    await repo.set_default("b")

    rows = await repo.list_rows()
    by_name = {r.name: r for r in rows}
    assert by_name["a"].is_default == 0
    assert by_name["b"].is_default == 1


async def test_set_default_idempotent_for_same_name(session: AsyncSession) -> None:
    """重复 set_default(同 name)幂等;仍只一行 is_default=1。"""
    repo = ProviderRepo(session)
    await repo.create(name="a", type="mock", options={})
    await repo.set_default("a")
    await repo.set_default("a")

    rows = await repo.list_rows()
    assert sum(r.is_default for r in rows) == 1


async def test_set_default_unknown_name_raises(session: AsyncSession) -> None:
    repo = ProviderRepo(session)
    await repo.create(name="a", type="mock", options={})
    with pytest.raises(ProviderNotFound):
        await repo.set_default("ghost")
    # 失败时不应误改其它行
    rows = await repo.list_rows()
    assert all(r.is_default == 0 for r in rows)


async def test_unset_default_clears_all(session: AsyncSession) -> None:
    repo = ProviderRepo(session)
    await repo.create(name="a", type="mock", options={})
    await repo.set_default("a")
    await repo.unset_default()

    assert await repo.get_default() is None
    rows = await repo.list_rows()
    assert all(r.is_default == 0 for r in rows)


async def test_unset_default_no_op_when_no_default(session: AsyncSession) -> None:
    """没默认时 unset_default 也不抛(no-op)。"""
    repo = ProviderRepo(session)
    await repo.create(name="a", type="mock", options={})
    await repo.unset_default()  # 不应抛
    assert await repo.get_default() is None

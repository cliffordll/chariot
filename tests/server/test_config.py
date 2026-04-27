"""ChariotConfig 测试 —— 数据形态 + DB 装载路径。

0.3.0 起源数据从 DB 装载(`ChariotConfig.from_db(session)`),0.2.x 的
TOML 装载(`ConfigLoader` / `from_dict`)整体删除。本文件只测剩下的:

- `ChariotConfig.empty()` / `active_entry()` 数据形态
- `ChariotConfig.from_db(session)` 装载 + active 一致性校验
- `ModelEntry` 数据形态(冻结 dataclass)

ModelRepo 自身的 CRUD 测试见 `test_model_repo.py`。
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.server.config import ChariotConfig, ConfigError, ModelEntry
from chariot.server.repository.model_repo import ModelRepo

# ---------- ChariotConfig.empty / active_entry(纯数据形态)----------


def test_empty_config_via_classmethod() -> None:
    c = ChariotConfig.empty()
    assert c.is_empty()
    assert c.models == ()
    assert c.active is None
    assert c.active_entry() is None


def test_active_entry_returns_matching_entry() -> None:
    e1 = ModelEntry(name="a", type="mock", options={})
    e2 = ModelEntry(name="b", type="anthropic", options={"model_id": "x"})
    c = ChariotConfig(models=(e1, e2), active="b")
    assert c.active_entry() is e2


def test_active_entry_inconsistency_raises() -> None:
    """绕过 from_db 直接构造 active 指向未知 name 的实例 —— 一致性兜底。"""
    c = ChariotConfig(
        models=(ModelEntry(name="a", type="mock", options={}),),
        active="ghost",
    )
    with pytest.raises(ConfigError, match="active"):
        c.active_entry()


# ---------- ChariotConfig.from_db ----------


async def test_from_db_empty_session_returns_empty(session: AsyncSession) -> None:
    """models 表空 → models=(),active=None。"""
    c = await ChariotConfig.from_db(session)
    assert c.is_empty()


async def test_from_db_loads_entries_and_active(session: AsyncSession) -> None:
    repo = ModelRepo(session)
    await repo.create(name="m1", type="mock", options={})
    await repo.create(
        name="claude",
        type="anthropic",
        options={"model_id": "claude-opus-4-5", "api_key": "sk-x"},
    )
    await repo.set_active("claude")

    c = await ChariotConfig.from_db(session)
    names = [e.name for e in c.models]
    assert names == ["m1", "claude"]
    assert c.active == "claude"
    entry = c.active_entry()
    assert entry is not None
    assert entry.type == "anthropic"
    assert entry.options["model_id"] == "claude-opus-4-5"


async def test_from_db_active_pointing_to_missing_entry_raises(
    session: AsyncSession,
) -> None:
    """settings.active_model 指向不存在的 entry → ConfigError(数据不一致)。"""
    repo = ModelRepo(session)
    await repo.create(name="m1", type="mock", options={})
    await repo.set_active("m1")
    # 删 m1 但不清 active(模拟数据损坏)
    await repo.delete("m1")

    with pytest.raises(ConfigError, match="active_model"):
        await ChariotConfig.from_db(session)


async def test_from_db_no_active_returns_models_only(session: AsyncSession) -> None:
    repo = ModelRepo(session)
    await repo.create(name="m1", type="mock", options={})
    c = await ChariotConfig.from_db(session)
    assert c.active is None
    assert len(c.models) == 1

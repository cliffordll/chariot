"""ChariotConfig 测试 —— 数据形态 + DB 装载路径。

0.3.1 active 概念删除后,ChariotConfig 退化成纯 entries 容器。
本文件覆盖:
- `ChariotConfig.empty()` / `find_entry()` 数据形态
- `ChariotConfig.from_db(session)` 装载
- `ModelEntry` 数据形态(冻结 dataclass,含 params 字段)

ModelRepo 自身的 CRUD 测试见 `test_model_repo.py`。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.config import ChariotConfig, ModelEntry
from chariot.repos.model_repo import ModelRepo

# ---------- ChariotConfig.empty / find_entry(纯数据形态)----------


def test_empty_config_via_classmethod() -> None:
    c = ChariotConfig.empty()
    assert c.is_empty()
    assert c.models == ()
    assert c.find_entry("anything") is None


def test_find_entry_returns_matching() -> None:
    e1 = ModelEntry(name="a", type="mock", options={})
    e2 = ModelEntry(name="b", type="anthropic", options={"model": "x"})
    c = ChariotConfig(models=(e1, e2))
    assert c.find_entry("b") is e2
    assert c.find_entry("a") is e1
    assert c.find_entry("ghost") is None


def test_model_entry_default_params() -> None:
    """params 默认空 dict,不传也合法。"""
    e = ModelEntry(name="x", type="mock", options={})
    assert e.params == {}


def test_model_entry_with_params() -> None:
    e = ModelEntry(
        name="x",
        type="anthropic",
        options={"model": "y"},
        params={"temperature": 0.5},
    )
    assert e.params == {"temperature": 0.5}


# ---------- ChariotConfig.from_db ----------


async def test_from_db_empty_session_returns_empty(session: AsyncSession) -> None:
    """models 表空 → models=()。"""
    c = await ChariotConfig.from_db(session)
    assert c.is_empty()


async def test_from_db_loads_entries(session: AsyncSession) -> None:
    repo = ModelRepo(session)
    await repo.create(name="m1", type="mock", options={})
    await repo.create(
        name="claude",
        type="anthropic",
        options={"model": "claude-opus-4-5", "api_key": "sk-x"},
        params={"temperature": 0.5},
    )

    c = await ChariotConfig.from_db(session)
    names = [e.name for e in c.models]
    assert names == ["m1", "claude"]
    found = c.find_entry("claude")
    assert found is not None
    assert found.type == "anthropic"
    assert found.options["model"] == "claude-opus-4-5"
    assert found.params == {"temperature": 0.5}

"""server 层测试共享 fixture。

提供:
- `session`:per-test 独立 SQLite + AsyncSession
- `isolate_model_registry`(autouse):每个用例前后 snapshot/restore
  `ModelRegistry._builders`,防止用例间互相污染
- `clean_agent_state`(autouse):每个用例结束清理 `Agent._current`,
  保证下一用例不被上一个的状态干扰
- `make_test_agent`:工厂 fixture,直接注入 `name → Model` 字典构造一个 Agent
  并注册成 `Agent.current()`(等同于 0.3.1 之前的 `Agent.install_test_models`,
  从生产类外移到测试侧)
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.server.agent import Agent
from chariot.server.database.session import dispose_db, init_db
from chariot.server.model.base import Model
from chariot.server.model.registry import ModelRegistry
from chariot.server.tool.registry import ToolRegistry


@pytest_asyncio.fixture
async def session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    """初始化一份独立 SQLite(走 migrations),yield 一个新 session,测试结束清理。"""
    db_path = tmp_path / "chariot.db"
    sm = await init_db(db_path)
    try:
        async with sm() as s:
            yield s
    finally:
        await dispose_db()


@pytest.fixture(autouse=True)
def isolate_model_registry() -> Iterator[None]:
    """快照 + 恢复 `ModelRegistry._builders`,防止用例间相互污染。

    autouse:server 测试无需显式声明,默认全员隔离。直接读写私有
    `_builders` —— conftest 与生产类是测试搭档,深耦合可接受;
    生产 API 收窄到 `register / build / known_types` 三件事。
    """
    snap = dict(ModelRegistry._builders)
    yield
    ModelRegistry._builders.clear()
    ModelRegistry._builders.update(snap)


@pytest.fixture(autouse=True)
def isolate_tool_registry() -> Iterator[None]:
    """快照 + 恢复 `ToolRegistry._builders`,防止用例间相互污染。

    模式跟 `isolate_model_registry` 完全一致(0.4.0 起 Tool 层加入)。
    """
    snap = dict(ToolRegistry._builders)
    yield
    ToolRegistry._builders.clear()
    ToolRegistry._builders.update(snap)


@pytest.fixture(autouse=True)
def clean_agent_state() -> Iterator[None]:
    """每个用例结束清理 `Agent` 类级单例(`_current` / `_config`),
    避免用例间状态串联。autouse,不需测试显式声明。"""
    yield
    Agent.uninstall()


@pytest.fixture
def make_test_agent() -> Callable[[dict[str, Model]], Agent]:
    """工厂 fixture:`agent = make_test_agent({"name": SpyModel(), ...})`。

    直接注入 `name → Model` 字典,绕开 `ModelRegistry.build` —— 测试想用任意
    spy / fake model 而不必先注册成一个 type。teardown 由 `clean_agent_state`
    autouse fixture 接管。
    """

    def _factory(models: dict[str, Model]) -> Agent:
        Agent.uninstall()
        agent = Agent()
        agent.models = dict(models)
        Agent._current = agent
        return agent

    return _factory

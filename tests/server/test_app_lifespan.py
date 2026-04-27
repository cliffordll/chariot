"""App lifespan e2e —— init_db + seed mock + Agent.install_from_config 端到端。

0.3.0 起 lifespan 不再读 ~/.chariot/config.toml,改成:
1. init_db(跑 migrations,含 v2 建 models / settings 表)
2. ModelRepo.seed_if_empty()(空表 → seed mock entry + active=mock)
3. ChariotConfig.from_db(session) 装载
4. Agent.install_from_config(config)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select

from chariot.server import app as app_module
from chariot.server.agent import Agent
from chariot.server.app import create_app, lifespan
from chariot.server.database.models import ModelRow, SettingRow
from chariot.server.database.session import _state as _db_state
from chariot.server.database.session import init_db as _real_init_db
from chariot.server.model.mock import MockModel
from chariot.server.repository.model_repo import ModelRepo


@pytest_asyncio.fixture
async def isolated_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> AsyncIterator[Path]:
    """把 lifespan 里的 init_db 重定向到 tmp_path,避免污染 ~/.chariot/chariot.db。"""
    db_path = tmp_path / "test.db"

    async def patched_init_db() -> None:
        await _real_init_db(db_path)

    monkeypatch.setattr(app_module, "init_db", patched_init_db)
    yield db_path


async def test_lifespan_first_run_seeds_mock(isolated_db: Path) -> None:
    """首次启动(空 DB)→ seed_if_empty 写入 mock entry + active=mock。"""
    app = create_app()
    Agent.uninstall()
    async with lifespan(app):
        current = Agent.current()
        assert isinstance(current.model, MockModel)
        assert Agent.active_name() == "mock"

        # DB 里也确实有 seed 行
        sm = _db_state.session_maker
        assert sm is not None
        async with sm() as s:
            rows = (await s.execute(select(ModelRow))).scalars().all()
            assert len(rows) == 1
            assert rows[0].name == "mock"
            assert rows[0].type == "mock"

            active_row = await s.get(SettingRow, "active_model")
            assert active_row is not None
            assert active_row.value == "mock"

    # lifespan 退出后 agent 应被 uninstall
    with pytest.raises(RuntimeError, match="未安装"):
        Agent.current()


async def test_lifespan_second_run_skips_seed(isolated_db: Path) -> None:
    """已有 entries(用户自己加过)→ seed_if_empty 不再插入,沿用已有状态。"""
    # 先用一次 init_db + 手工写一条 entry,模拟"用户已加过 model"
    await _real_init_db(isolated_db)
    sm = _db_state.session_maker
    assert sm is not None
    async with sm() as s:
        await ModelRepo(s).create(name="my-claude", type="mock", options={})
        await ModelRepo(s).set_active("my-claude")
    # dispose 让 lifespan 自己重新 init_db
    await app_module.dispose_db()

    app = create_app()
    Agent.uninstall()
    async with lifespan(app):
        # active 沿用 my-claude(seed_if_empty 没动表)
        assert Agent.active_name() == "my-claude"
        # 表里只有 my-claude,没插入 mock seed
        sm2 = _db_state.session_maker
        assert sm2 is not None
        async with sm2() as s:
            rows = (await s.execute(select(ModelRow))).scalars().all()
            assert len(rows) == 1
            assert rows[0].name == "my-claude"


async def test_lifespan_inconsistent_active_raises(isolated_db: Path) -> None:
    """settings.active_model 指向不存在的 entry → ConfigError 上冒,server 不起来。

    构造方式:create 两条 entry + set_active 第一条 + delete 第一条。表非空所以
    seed_if_empty 不会触发(也就不会修复 active),lifespan 读到不一致 → raise。
    """
    from chariot.server.config import ConfigError

    await _real_init_db(isolated_db)
    sm = _db_state.session_maker
    assert sm is not None
    async with sm() as s:
        await ModelRepo(s).create(name="real", type="mock", options={})
        await ModelRepo(s).create(name="other", type="mock", options={})
        await ModelRepo(s).set_active("real")
        await ModelRepo(s).delete("real")  # active 还指着 'real',但 entry 已删
    await app_module.dispose_db()

    app = create_app()
    Agent.uninstall()
    with pytest.raises(ConfigError, match="active_model"):
        async with lifespan(app):
            pass  # pragma: no cover —— 不应到达

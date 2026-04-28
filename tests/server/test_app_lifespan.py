"""App lifespan e2e —— init_db + seed mock + 4 tool fixtures + Agent.install_from_config 端到端。

0.4.0 起(M.3):
1. init_db(跑 migrations,含 v4 加 conversations / messages / tools 三表)
2. ModelRepo.seed_if_empty()(空表 → seed mock entry)
3. ToolRepo.seed_if_empty()(空表 → seed 4 条 disabled fixture)
4. ChariotConfig.from_db + ToolConfig.from_db
5. Agent.install_from_config(model_config, tool_config) 构建 name → Model + name → Tool
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chariot.server import app as app_module
from chariot.server.agent import Agent
from chariot.server.app import create_app, lifespan
from chariot.server.database.models import ModelRow, ToolRow
from chariot.server.database.session import DBState
from chariot.server.database.session import init_db as _real_init_db
from chariot.server.model.mock import MockModel
from chariot.server.repository.model_repo import ModelRepo
from chariot.server.repository.tool_repo import ToolRepo


@pytest_asyncio.fixture
async def isolated_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> AsyncIterator[Path]:
    """把 lifespan 里的 init_db 重定向到 tmp_path,避免污染 ~/.chariot/chariot.db。"""
    db_path = tmp_path / "test.db"

    async def patched_init_db() -> async_sessionmaker[AsyncSession]:
        return await _real_init_db(db_path)

    monkeypatch.setattr(app_module, "init_db", patched_init_db)
    yield db_path


async def test_lifespan_first_run_seeds_mock(isolated_db: Path) -> None:
    """首次启动(空 DB)→ seed_if_empty 写入 mock entry,Agent.models 含 mock。"""
    app = create_app()
    Agent.uninstall()
    async with lifespan(app):
        current = Agent.current()
        # mock entry 应被 build 进字典
        assert "mock" in current.models
        assert isinstance(current.models["mock"], MockModel)

        # DB 里也确实有 seed 行
        sm = DBState.session_maker
        assert sm is not None
        async with sm() as s:
            rows = (await s.execute(select(ModelRow))).scalars().all()
            assert len(rows) == 1
            assert rows[0].name == "mock"
            assert rows[0].type == "mock"

    # lifespan 退出后 agent 应被 uninstall
    with pytest.raises(RuntimeError, match="未安装"):
        Agent.current()


async def test_lifespan_first_run_seeds_4_tool_fixtures(isolated_db: Path) -> None:
    """首次启动 → ToolRepo.seed_if_empty 写入 4 条 disabled fixture。"""
    app = create_app()
    Agent.uninstall()
    async with lifespan(app):
        # tools 表里应有 4 条 fixture,全部 disabled
        sm = DBState.session_maker
        assert sm is not None
        async with sm() as s:
            rows = (await s.execute(select(ToolRow))).scalars().all()
            names = sorted(r.name for r in rows)
            assert names == ["http_get", "list_dir", "read_file", "shell_exec"]
            assert all(r.enabled == 0 for r in rows)
        # Agent.tools 字典空(没启用任何 tool)
        assert Agent.current().tools == {}


async def test_lifespan_with_enabled_tool_builds_into_agent(isolated_db: Path) -> None:
    """已 enable 的 tool → Agent.tools 字典含该 tool 实例。"""
    # 先 init + seed + 启 read_file
    await _real_init_db(isolated_db)
    sm = DBState.session_maker
    assert sm is not None
    async with sm() as s:
        await ToolRepo(s).seed_if_empty()
        await ToolRepo(s).update("read_file", enabled=True)
    await app_module.dispose_db()

    app = create_app()
    Agent.uninstall()
    async with lifespan(app):
        agent = Agent.current()
        assert "read_file" in agent.tools
        assert "list_dir" not in agent.tools  # 仍 disabled


async def test_lifespan_second_run_skips_seed(isolated_db: Path) -> None:
    """已有 entries(用户自己加过)→ seed_if_empty 不再插入,沿用已有状态。"""
    # 先用一次 init_db + 手工写一条 entry,模拟"用户已加过 model"
    await _real_init_db(isolated_db)
    sm = DBState.session_maker
    assert sm is not None
    async with sm() as s:
        await ModelRepo(s).create(name="my-claude", type="mock", options={})
    # dispose 让 lifespan 自己重新 init_db
    await app_module.dispose_db()

    app = create_app()
    Agent.uninstall()
    async with lifespan(app):
        agent = Agent.current()
        # entries 沿用 my-claude(seed_if_empty 没动表)
        assert "my-claude" in agent.models
        assert "mock" not in agent.models  # 没新 seed
        # 表里只有 my-claude
        sm2 = DBState.session_maker
        assert sm2 is not None
        async with sm2() as s:
            rows = (await s.execute(select(ModelRow))).scalars().all()
            assert len(rows) == 1
            assert rows[0].name == "my-claude"

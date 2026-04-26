"""App lifespan e2e —— `ConfigLoader.load()` → `Agent.install_from_config()` 端到端。

覆盖 B.3:
- 无 config(env 指向不存在文件)→ MockModel fallback,行为兼容 0.1.0
- 有 config 含 active mock 条目 → 走 ModelRegistry.build,仍是 MockModel 实例
- lifespan 正常退出后 agent 被 uninstall(资源释放)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from chariot.server import app as app_module
from chariot.server.agent import Agent
from chariot.server.app import create_app, lifespan
from chariot.server.config import ConfigError
from chariot.server.database.session import init_db as _real_init_db
from chariot.server.model.mock import MockModel


@pytest_asyncio.fixture
async def isolated_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> AsyncIterator[Path]:
    """把 lifespan 里的 init_db 重定向到 tmp_path,避免污染 ~/.chariot/chariot.db。"""
    db_path = tmp_path / "test.db"

    async def patched_init_db() -> None:
        await _real_init_db(db_path)

    monkeypatch.setattr(app_module, "init_db", patched_init_db)
    yield db_path


async def test_lifespan_no_config_uses_mock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    isolated_db: Path,
) -> None:
    """env 指向不存在路径 → ChariotConfig.empty() → MockModel fallback。"""
    monkeypatch.setenv("CHARIOT_CONFIG", str(tmp_path / "missing.toml"))

    app = create_app()
    Agent.uninstall()
    async with lifespan(app):
        current = Agent.current()
        assert isinstance(current.model, MockModel)
    # lifespan 退出后 agent 应被 uninstall
    with pytest.raises(RuntimeError, match="未安装"):
        Agent.current()


async def test_lifespan_with_mock_config_uses_registry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    isolated_db: Path,
) -> None:
    """配置里 active=mock-default,经 ModelRegistry.build 仍生成 MockModel。"""
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[[models]]\nname = "mock-default"\ntype = "mock"\n\n[active]\nmodel = "mock-default"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CHARIOT_CONFIG", str(cfg))

    app = create_app()
    Agent.uninstall()
    async with lifespan(app):
        current = Agent.current()
        assert isinstance(current.model, MockModel)


async def test_lifespan_with_unknown_type_propagates_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    isolated_db: Path,
) -> None:
    """配置里 active 指向未注册 type → ConfigError 上冒,server 不起来。"""
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[[models]]\nname = "ghost"\ntype = "no_such_type"\n\n[active]\nmodel = "ghost"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CHARIOT_CONFIG", str(cfg))

    app = create_app()
    Agent.uninstall()
    with pytest.raises(ConfigError, match="no_such_type"):
        async with lifespan(app):
            pass  # pragma: no cover —— 不应到达

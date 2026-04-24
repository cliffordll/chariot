"""server 层测试共享 fixture:per-test 独立 SQLite + session。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.server.database.session import _state as _db_state
from chariot.server.database.session import dispose_db, init_db


@pytest_asyncio.fixture
async def session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    """初始化一份独立 SQLite(走 migrations),yield 一个新 session,测试结束清理。"""
    db_path = tmp_path / "chariot.db"
    await init_db(db_path)
    try:
        assert _db_state.session_maker is not None
        async with _db_state.session_maker() as s:
            yield s
    finally:
        await dispose_db()

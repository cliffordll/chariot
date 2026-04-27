"""SQLite 异步 engine / session 工厂(SQLAlchemy 2.x async)。

启动时 `init_db()` 建目录 / engine / 跑 migrations / session maker;
关闭时 `dispose_db()` 释放连接池。
migrations 用 SA 跑,aiosqlite 只作 `sqlite+aiosqlite://` 驱动依赖。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DEFAULT_DB_PATH = Path.home() / ".chariot" / "chariot.db"
CURRENT_SCHEMA_VERSION = 3


@dataclass
class _DBState:
    engine: AsyncEngine | None = None
    session_maker: async_sessionmaker[AsyncSession] | None = None


_state = _DBState()


def _db_url(db_path: Path) -> str:
    # as_posix 把 Windows 反斜杠转正斜杠,避免 URL 解析问题
    return f"sqlite+aiosqlite:///{db_path.as_posix()}"


def _split_sql_statements(sql: str) -> list[str]:
    """按 `;` 切 SQL 文件;跳过 `--` 开头的整行注释。"""
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def _list_migrations() -> list[tuple[int, Path]]:
    """扫 migrations/NNN_*.sql 按编号升序返回;检测重复。"""
    dir_ = Path(__file__).parent / "migrations"
    files = [(int(p.name[:3]), p) for p in dir_.glob("[0-9][0-9][0-9]_*.sql")]
    nums = [n for n, _ in files]
    if len(set(nums)) != len(nums):
        raise RuntimeError(f"migrations/ 有重复编号:{sorted(nums)}")
    return sorted(files, key=lambda x: x[0])


async def _maybe_run_migrations(engine: AsyncEngine) -> None:
    """按 user_version 差量跑 migration,每个文件一个事务。"""
    migrations = _list_migrations()
    if not migrations:
        raise RuntimeError("migrations/ 下没有 NNN_*.sql 文件")

    # 防止程序员改了常量忘加 SQL 文件,或反之
    max_n = migrations[-1][0]
    if max_n != CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"CURRENT_SCHEMA_VERSION={CURRENT_SCHEMA_VERSION} 与最高 migration 编号 {max_n} "
            "不一致,更新 session.py 或补齐 migration 文件"
        )

    # SQLite 空文件会被自动创建,首次读 PRAGMA user_version 为 0
    async with engine.connect() as conn:
        result = await conn.execute(text("PRAGMA user_version"))
        row = result.fetchone()
    current = int(row[0]) if row else 0

    if current > CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"DB schema version {current} 比代码支持的 {CURRENT_SCHEMA_VERSION} 还新,拒启动"
        )
    if current == CURRENT_SCHEMA_VERSION:
        return

    # 每个 migration 文件一个事务,失败只回滚当前文件
    for n, path in migrations:
        if n <= current:
            continue
        statements = _split_sql_statements(path.read_text(encoding="utf-8"))
        async with engine.begin() as conn:
            for stmt in statements:
                await conn.execute(text(stmt))


async def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    """建目录 + engine + 跑 migrations + 绑 session_maker。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(_db_url(db_path))
    await _maybe_run_migrations(engine)
    _state.engine = engine
    # expire_on_commit=False:commit 后对象属性不失效,避免响应序列化时 lazy reload
    _state.session_maker = async_sessionmaker(engine, expire_on_commit=False)


async def dispose_db() -> None:
    """释放连接池;SQLite WAL checkpoint 在最后一个连接关闭时触发。"""
    if _state.engine is not None:
        await _state.engine.dispose()
    _state.engine = None
    _state.session_maker = None


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖:每请求一个独立 session,退出时自动关(未 commit 则 rollback)。"""
    if _state.session_maker is None:
        raise RuntimeError("DB 未初始化,先调 init_db()")
    async with _state.session_maker() as session:
        yield session


def get_session_maker() -> async_sessionmaker[AsyncSession] | None:
    """给非 FastAPI-scoped 路径(service 层后台写入)拿 session_maker。未 init 返 None。"""
    return _state.session_maker


# 公共 FastAPI 依赖别名:avoid 各模块重复定义。
SessionDep = Annotated[AsyncSession, Depends(get_session)]

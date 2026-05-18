"""SQLite 异步 engine / session 工厂(SQLAlchemy 2.x async)。

启动时 `init_db()` 建目录 / engine / 跑 migrations / session maker;
关闭时 `dispose_db()` 释放连接池。
migrations 用 SA 跑,aiosqlite 只作 `sqlite+aiosqlite://` 驱动依赖。

封装
----
单例状态(engine + session_maker)挂在 `DBState` ClassVar 上,生命周期由
`install / dispose` 两个 classmethod 管。模块级零可变变量、零自由函数(只剩
纯静态 SQL 解析 helper);公开 API `init_db / dispose_db / get_session_maker /
get_session` 均是 `DBState.<method>` 的薄别名,调用方无感。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import ClassVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from ulid import ULID

DEFAULT_DB_PATH = Path.home() / ".chariot" / "chariot.db"
CURRENT_SCHEMA_VERSION = 7


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

    # 0.8.9 provider identity 清理后把 002..028 历史链压成单个 squashed migration。
    # 现网只需要兼容:
    # - v0/v1:空库或 0.1.0 `logs` 单表库,按 001 + 002 升到当前
    # - v28/v29:旧开发库已在 squash 前最终 schema,收敛回 v2 后继续补最新 migration
    # 中间版本链(2..27)在 squash 后不再保留自动升级承诺。
    if current in {28, 29}:
        async with engine.begin() as conn:
            await conn.execute(text("PRAGMA user_version = 2"))
        current = 2
    if current > CURRENT_SCHEMA_VERSION:
        raise RuntimeError(f"DB schema version {current} 比代码支持的 {CURRENT_SCHEMA_VERSION} 还新,拒启动")
    if current not in {0, 1, 2, 3, 4, 5, 6, CURRENT_SCHEMA_VERSION}:
        raise RuntimeError(
            f"DB schema version {current} 不在 squash 后支持的自动升级集合内; "
            "当前仅支持 v0/v1 新装或历史 0.1.0 库、以及已到 v28/v29 的现有库"
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
                try:
                    await conn.execute(text(stmt))
                except Exception as exc:
                    # SQLite 迁移幂等容错:ALTER TABLE ADD COLUMN 重复时自动跳过,
                    # 避免"部分执行后 user_version 未更新"导致的死循环
                    msg = str(exc)
                    if "duplicate column name" in msg.lower() or "already exists" in msg.lower():
                        continue
                    raise


async def _ensure_agent_profile_identity_schema(engine: AsyncEngine) -> None:
    statements = [
        "ALTER TABLE agent_profiles ADD COLUMN id TEXT",
        "CREATE UNIQUE INDEX idx_agent_profiles_id ON agent_profiles(id)",
        "ALTER TABLE tasks ADD COLUMN agent_profile_id TEXT",
        "CREATE INDEX idx_tasks_agent_profile_id ON tasks(agent_profile_id)",
        "ALTER TABLE scheduled_jobs ADD COLUMN agent_profile_id TEXT",
        "CREATE INDEX idx_scheduled_jobs_agent_profile_id ON scheduled_jobs(agent_profile_id)",
    ]
    async with engine.begin() as conn:
        for stmt in statements:
            try:
                await conn.execute(text(stmt))
            except Exception as exc:
                msg = str(exc).lower()
                if "duplicate column name" in msg or "already exists" in msg:
                    continue
                raise


async def _backfill_agent_profile_identity(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        rows = (await conn.execute(text("SELECT name, id FROM agent_profiles ORDER BY name"))).mappings().all()
        for row in rows:
            if row["id"]:
                continue
            await conn.execute(
                text("UPDATE agent_profiles SET id = :id WHERE name = :name"),
                {"id": str(ULID()), "name": row["name"]},
            )
        await conn.execute(
            text(
                """
                UPDATE tasks
                SET agent_profile_id = (
                    SELECT ap.id FROM agent_profiles AS ap
                    WHERE ap.name = tasks.agent_profile
                )
                WHERE agent_profile IS NOT NULL
                  AND (agent_profile_id IS NULL OR agent_profile_id = '')
                """
            )
        )
        await conn.execute(
            text(
                """
                UPDATE scheduled_jobs
                SET agent_profile_id = (
                    SELECT ap.id FROM agent_profiles AS ap
                    WHERE ap.name = scheduled_jobs.agent_profile
                )
                WHERE agent_profile IS NOT NULL
                  AND (agent_profile_id IS NULL OR agent_profile_id = '')
                """
            )
        )


async def _ensure_auxiliary_identity_schema(engine: AsyncEngine) -> None:
    statements = [
        "ALTER TABLE auxiliary_clients ADD COLUMN id TEXT",
        "CREATE UNIQUE INDEX idx_auxiliary_clients_id ON auxiliary_clients(id)",
    ]
    async with engine.begin() as conn:
        for stmt in statements:
            try:
                await conn.execute(text(stmt))
            except Exception as exc:
                msg = str(exc).lower()
                if "duplicate column name" in msg or "already exists" in msg:
                    continue
                raise


async def _backfill_auxiliary_identity(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        rows = (await conn.execute(text("SELECT name, id FROM auxiliary_clients ORDER BY name"))).mappings().all()
        for row in rows:
            if row["id"]:
                continue
            await conn.execute(
                text("UPDATE auxiliary_clients SET id = :id WHERE name = :name"),
                {"id": str(ULID()), "name": row["name"]},
            )


async def _ensure_toolset_identity_schema(engine: AsyncEngine) -> None:
    statements = [
        "ALTER TABLE toolsets ADD COLUMN id TEXT",
        "CREATE UNIQUE INDEX idx_toolsets_id ON toolsets(id)",
        "ALTER TABLE toolset_members ADD COLUMN toolset_id TEXT",
        "CREATE INDEX idx_toolset_members_toolset_id ON toolset_members(toolset_id)",
        "ALTER TABLE agent_profiles ADD COLUMN toolset_id TEXT",
        "CREATE INDEX idx_agent_profiles_toolset_id ON agent_profiles(toolset_id)",
    ]
    async with engine.begin() as conn:
        for stmt in statements:
            try:
                await conn.execute(text(stmt))
            except Exception as exc:
                msg = str(exc).lower()
                if "duplicate column name" in msg or "already exists" in msg:
                    continue
                raise


async def _backfill_toolset_identity(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        rows = (await conn.execute(text("SELECT name, id FROM toolsets ORDER BY name"))).mappings().all()
        for row in rows:
            if row["id"]:
                continue
            await conn.execute(
                text("UPDATE toolsets SET id = :id WHERE name = :name"),
                {"id": str(ULID()), "name": row["name"]},
            )
        await conn.execute(
            text(
                """
                UPDATE toolset_members
                SET toolset_id = (
                    SELECT ts.id FROM toolsets AS ts
                    WHERE ts.name = toolset_members.toolset_name
                )
                WHERE toolset_name IS NOT NULL
                  AND (toolset_id IS NULL OR toolset_id = '')
                """
            )
        )
        await conn.execute(
            text(
                """
                UPDATE agent_profiles
                SET toolset_id = (
                    SELECT ts.id FROM toolsets AS ts
                    WHERE ts.name = agent_profiles.tool_profile
                )
                WHERE tool_profile IS NOT NULL
                  AND (toolset_id IS NULL OR toolset_id = '')
                """
            )
        )


async def _normalize_agent_profile_prompt_id_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        columns = (await conn.execute(text("PRAGMA table_info(agent_profiles)"))).mappings().all()
        column_names = {str(row["name"]) for row in columns}

        if "prompt_id" not in column_names:
            await conn.execute(text("ALTER TABLE agent_profiles ADD COLUMN prompt_id TEXT"))
            await conn.execute(text("CREATE INDEX idx_agent_profiles_prompt_id ON agent_profiles(prompt_id)"))
            column_names.add("prompt_id")

        if "prompt_bundle" in column_names:
            await conn.execute(
                text(
                    """
                    UPDATE agent_profiles
                    SET prompt_id = (
                        SELECT pb.id FROM prompt_bundles AS pb
                        WHERE pb.name = agent_profiles.prompt_bundle
                    )
                    WHERE prompt_bundle IS NOT NULL
                      AND (prompt_id IS NULL OR prompt_id = '')
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE TABLE agent_profiles__new (
                        name TEXT PRIMARY KEY,
                        id TEXT UNIQUE,
                        role TEXT NOT NULL,
                        prompt_id TEXT,
                        tool_profile TEXT,
                        toolset_id TEXT,
                        provider_id TEXT,
                        budget TEXT NOT NULL DEFAULT '{}',
                        meta TEXT NOT NULL DEFAULT '{}',
                        reflection_enabled INTEGER NOT NULL DEFAULT 0,
                        reflection_max_retries INTEGER NOT NULL DEFAULT 2,
                        default_skill TEXT,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO agent_profiles__new (
                        name,
                        id,
                        role,
                        prompt_id,
                        tool_profile,
                        toolset_id,
                        provider_id,
                        budget,
                        meta,
                        reflection_enabled,
                        reflection_max_retries,
                        default_skill,
                        created_at,
                        updated_at
                    )
                    SELECT
                        name,
                        id,
                        role,
                        prompt_id,
                        tool_profile,
                        toolset_id,
                        provider_id,
                        budget,
                        meta,
                        reflection_enabled,
                        reflection_max_retries,
                        default_skill,
                        created_at,
                        updated_at
                    FROM agent_profiles
                    """
                )
            )
            await conn.execute(text("DROP TABLE agent_profiles"))
            await conn.execute(text("ALTER TABLE agent_profiles__new RENAME TO agent_profiles"))
            await conn.execute(text("CREATE UNIQUE INDEX idx_agent_profiles_id ON agent_profiles(id)"))
            await conn.execute(text("CREATE INDEX idx_agent_profiles_role ON agent_profiles(role)"))
            await conn.execute(text("CREATE INDEX idx_agent_profiles_prompt_id ON agent_profiles(prompt_id)"))
            await conn.execute(text("CREATE INDEX idx_agent_profiles_toolset_id ON agent_profiles(toolset_id)"))


async def _ensure_job_identity_schema(engine: AsyncEngine) -> None:
    statements = [
        "ALTER TABLE scheduled_jobs ADD COLUMN id TEXT",
        "CREATE UNIQUE INDEX idx_scheduled_jobs_id ON scheduled_jobs(id)",
        "ALTER TABLE job_runs ADD COLUMN job_id TEXT",
        "CREATE INDEX idx_job_runs_job_id ON job_runs(job_id)",
    ]
    async with engine.begin() as conn:
        for stmt in statements:
            try:
                await conn.execute(text(stmt))
            except Exception as exc:
                msg = str(exc).lower()
                if "duplicate column name" in msg or "already exists" in msg:
                    continue
                raise


async def _backfill_job_identity(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        rows = (await conn.execute(text("SELECT name, id FROM scheduled_jobs ORDER BY name"))).mappings().all()
        for row in rows:
            if row["id"]:
                continue
            await conn.execute(
                text("UPDATE scheduled_jobs SET id = :id WHERE name = :name"),
                {"id": str(ULID()), "name": row["name"]},
            )
        await conn.execute(
            text(
                """
                UPDATE job_runs
                SET job_id = (
                    SELECT sj.id FROM scheduled_jobs AS sj
                    WHERE sj.name = job_runs.job_name
                )
                WHERE job_name IS NOT NULL
                  AND (job_id IS NULL OR job_id = '')
                """
            )
        )


class DBState:
    """DB 连接 + session 工厂的类级单例(取代旧 `_state = _DBState()` 模块级变量)。

    生命周期:
    - `install(db_path)`:启动时调,建 engine + 跑 migrations + 装 sm
    - `dispose()`:关闭时调,释放连接池
    - `session_maker_or_none()`:fail-soft 路径(后台 LogWriter 在 startup 未跑完
      时也可能被调,这里允许返 None,调用方决定怎么兜底)
    - `session_iter()`:async-generator dep,每次产一个独立 session;sm 未装载
      直接 raise(请求路径不可静默)
    """

    engine: ClassVar[AsyncEngine | None] = None
    session_maker: ClassVar[async_sessionmaker[AsyncSession] | None] = None

    @classmethod
    async def install(cls, db_path: Path = DEFAULT_DB_PATH) -> async_sessionmaker[AsyncSession]:
        """建目录 + engine + 跑 migrations + 绑 session_maker;返回 session_maker。

        幂等(0.6.5 起):若已 install 同一 db_path,直接返已有 sessionmaker;
        若 db_path 切换(测试场景),先 dispose 老 engine 再装新的。这让多个
        AIAgent 实例(per-session 缓存)共享同一进程的 engine + 连接池。
        """
        if cls.engine is not None and cls.session_maker is not None:
            existing_url = str(cls.engine.url)
            new_url = _db_url(db_path)
            if existing_url == new_url:
                return cls.session_maker
            await cls.dispose()  # db_path 切换 → 释放老 engine

        db_path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_async_engine(_db_url(db_path))
        await _maybe_run_migrations(engine)
        await _ensure_agent_profile_identity_schema(engine)
        await _backfill_agent_profile_identity(engine)
        await _ensure_auxiliary_identity_schema(engine)
        await _backfill_auxiliary_identity(engine)
        await _ensure_toolset_identity_schema(engine)
        await _backfill_toolset_identity(engine)
        await _normalize_agent_profile_prompt_id_schema(engine)
        await _ensure_job_identity_schema(engine)
        await _backfill_job_identity(engine)
        cls.engine = engine
        # expire_on_commit=False:commit 后对象属性不失效,避免响应序列化时 lazy reload
        sm = async_sessionmaker(engine, expire_on_commit=False)
        cls.session_maker = sm
        return sm

    @classmethod
    async def dispose(cls) -> None:
        """释放连接池;SQLite WAL checkpoint 在最后一个连接关闭时触发。"""
        if cls.engine is not None:
            await cls.engine.dispose()
        cls.engine = None
        cls.session_maker = None

    @classmethod
    def session_maker_or_none(cls) -> async_sessionmaker[AsyncSession] | None:
        """给后台路径(LogWriter)拿 session_maker。未 init 返 None。"""
        return cls.session_maker

    @classmethod
    async def session_iter(cls) -> AsyncIterator[AsyncSession]:
        """async-generator session dep:每次产一个独立 session,退出时自动关。

        未 commit 则 rollback。
        """
        if cls.session_maker is None:
            raise RuntimeError("DB 未初始化,先调 init_db()")
        async with cls.session_maker() as session:
            yield session


# 模块级公开 API(别名指向 classmethod;调用方 import 链路不变)
init_db = DBState.install
dispose_db = DBState.dispose
get_session_maker = DBState.session_maker_or_none
get_session = DBState.session_iter

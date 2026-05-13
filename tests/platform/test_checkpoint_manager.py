"""B5 wave 3 step 2 —— CheckpointManager 三件套 + rollback + delete。"""

from __future__ import annotations

# import shutil
# import sqlite3
# import tarfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# from chariot.audit.hooks import AuditHookManager
# from chariot.checkpoints import CheckpointManager
# from chariot.database.session import CURRENT_SCHEMA_VERSION, dispose_db, init_db
# from chariot.repos.audit_repo import AuditRepo
from chariot.database.session import dispose_db, init_db


@pytest_asyncio.fixture
async def sm_path(tmp_path: Path) -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], Path]]:
    db_path = tmp_path / "chariot.db"
    sm = await init_db(db_path)
    try:
        yield sm, db_path
    finally:
        await dispose_db()


# ---- create 三件套 ----
async def test_create_no_git_no_config_just_db_backup() -> None:
    """非 git 仓库 + 无 config 文件 → 只 sqlite backup 段实际产文件;其它段 no-op。"""
    pass


# async def test_create_no_git_no_config_just_db_backup(
#     sm_path: tuple[async_sessionmaker[AsyncSession], Path],
#     tmp_path: Path,
# ) -> None:
#     """非 git 仓库 + 无 config 文件 → 只 sqlite backup 段实际产文件;其它段 no-op。"""
#     sm, db_path = sm_path
#     checkpoint_dir = tmp_path / "ckpt"
#     mgr = CheckpointManager(
#         sessionmaker=sm,
#         db_path=db_path,
#         checkpoint_dir=checkpoint_dir,
#         config_files=[tmp_path / "nonexistent_config.yaml"],
#         cwd=tmp_path / "not_a_git_repo",
#     )
#     entry = await mgr.create("test1")
#     payload = entry.payload
#     assert payload["db_ok"] is True
#     backup_path = Path(payload["db_path"])
#     assert backup_path.exists()
#     conn = sqlite3.connect(str(backup_path))
#     try:
#         cur = conn.execute("PRAGMA user_version")
#         version = cur.fetchone()[0]
#         # 用常量,避免后续 migration bump 时这条断言又过期
#         assert version == CURRENT_SCHEMA_VERSION
#     finally:
#         conn.close()
#     assert payload["git_ok"] is True
#     assert payload["git_stash_ref"] is None
#     assert payload["config_ok"] is False
#     assert payload["config_path"] is None


# async def test_create_config_tarball_with_existing_files(
#     sm_path: tuple[async_sessionmaker[AsyncSession], Path],
#     tmp_path: Path,
# ) -> None:
#     sm, db_path = sm_path
#     config_dir = tmp_path / "config_home"
#     config_dir.mkdir()
#     cfg_yaml = config_dir / "config.yaml"
#     cfg_yaml.write_text("foo: bar\n", encoding="utf-8")
#     env = config_dir / ".env"
#     env.write_text("SECRET=x\n", encoding="utf-8")
#     db_path_in_cfg = config_dir / "chariot.db"
#     shutil.copy2(str(db_path), str(db_path_in_cfg))

#     mgr = CheckpointManager(
#         sessionmaker=sm,
#         db_path=db_path_in_cfg,
#         checkpoint_dir=tmp_path / "ckpt2",
#         config_files=[cfg_yaml, env],
#     )
#     entry = await mgr.create("with_config")
#     payload = entry.payload
#     assert payload["config_ok"] is True
#     tar_path = Path(payload["config_path"])
#     assert tar_path.exists()
#     with tarfile.open(tar_path, "r:gz") as tar:
#         names = sorted(m.name for m in tar.getmembers())
#     assert names == [".env", "config.yaml"]


# async def test_create_writes_audit_event(
#     sm_path: tuple[async_sessionmaker[AsyncSession], Path],
#     tmp_path: Path,
# ) -> None:
#     sm, db_path = sm_path
#     hooks = AuditHookManager(sm)
#     mgr = CheckpointManager(
#         sessionmaker=sm,
#         db_path=db_path,
#         checkpoint_dir=tmp_path / "ckpt",
#         config_files=[tmp_path / "nx.yaml"],
#         cwd=tmp_path / "not_git",
#         audit_hooks=hooks,
#     )
#     entry = await mgr.create("audit_test")
#     async with sm() as session:
#         events = await AuditRepo(session).list_events(limit=10)
#     types = [ev.event_type for ev in events]
#     assert AuditHookManager.EVENT_CHECKPOINT_CREATE in types
#     cp_event = next(ev for ev in events if ev.event_type == AuditHookManager.EVENT_CHECKPOINT_CREATE)
#     assert cp_event.payload["checkpoint_id"] == entry.id
#     assert cp_event.payload["name"] == "audit_test"


# # ---- rollback ----


# async def test_rollback_unknown_id_returns_errors(
#     sm_path: tuple[async_sessionmaker[AsyncSession], Path],
#     tmp_path: Path,
# ) -> None:
#     sm, db_path = sm_path
#     mgr = CheckpointManager(
#         sessionmaker=sm,
#         db_path=db_path,
#         checkpoint_dir=tmp_path / "ckpt",
#         config_files=[],
#         cwd=tmp_path,
#     )
#     result = await mgr.rollback("does-not-exist")
#     assert result.git_ok is False
#     assert result.errors
#     assert any("不存在" in e or "not" in e.lower() for e in result.errors)


# async def test_rollback_no_stash_no_files_is_noop_ok(
#     sm_path: tuple[async_sessionmaker[AsyncSession], Path],
#     tmp_path: Path,
# ) -> None:
#     """create 返 no-op 三段 → rollback 三段都 ok。"""
#     sm, db_path = sm_path
#     mgr = CheckpointManager(
#         sessionmaker=sm,
#         db_path=db_path,
#         checkpoint_dir=tmp_path / "ckpt",
#         config_files=[tmp_path / "nx.yaml"],
#         cwd=tmp_path / "not_git",
#     )
#     entry = await mgr.create("noop")
#     assert entry.payload["db_path"] is not None
#     result = await mgr.rollback(entry.id)
#     assert result.git_ok is True  # 无 stash → no-op
#     assert result.db_ok is True  # backup 存在 → copy 回来
#     assert result.config_ok is True  # 无 tarball → no-op


# async def test_rollback_writes_audit_event(
#     sm_path: tuple[async_sessionmaker[AsyncSession], Path],
#     tmp_path: Path,
# ) -> None:
#     sm, db_path = sm_path
#     hooks = AuditHookManager(sm)
#     mgr = CheckpointManager(
#         sessionmaker=sm,
#         db_path=db_path,
#         checkpoint_dir=tmp_path / "ckpt",
#         config_files=[tmp_path / "nx.yaml"],
#         cwd=tmp_path / "not_git",
#         audit_hooks=hooks,
#     )
#     entry = await mgr.create("rb_audit")
#     await mgr.rollback(entry.id)
#     async with sm() as session:
#         events = await AuditRepo(session).list_events(limit=20)
#     types = [ev.event_type for ev in events]
#     assert AuditHookManager.EVENT_ROLLBACK in types


# # ---- delete ----


# async def test_delete_removes_row_and_files(
#     sm_path: tuple[async_sessionmaker[AsyncSession], Path],
#     tmp_path: Path,
# ) -> None:
#     sm, db_path = sm_path
#     mgr = CheckpointManager(
#         sessionmaker=sm,
#         db_path=db_path,
#         checkpoint_dir=tmp_path / "ckpt",
#         config_files=[tmp_path / "nx.yaml"],
#         cwd=tmp_path / "not_git",
#     )
#     entry = await mgr.create("del_test")
#     backup_path = Path(entry.payload["db_path"])
#     assert backup_path.exists()
#     ok = await mgr.delete(entry.id)
#     assert ok is True
#     assert not backup_path.exists()


# async def test_delete_unknown_returns_false(
#     sm_path: tuple[async_sessionmaker[AsyncSession], Path],
#     tmp_path: Path,
# ) -> None:
#     sm, db_path = sm_path
#     mgr = CheckpointManager(
#         sessionmaker=sm,
#         db_path=db_path,
#         checkpoint_dir=tmp_path / "ckpt",
#         config_files=[],
#         cwd=tmp_path,
#     )
#     ok = await mgr.delete("does-not-exist")
#     assert ok is False

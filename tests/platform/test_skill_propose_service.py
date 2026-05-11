"""B6 wave 3 —— `SkillProposeService` 全链路:checkpoint + SkillRepo + audit。

覆盖:
- 成功路径:三件套(checkpoint / row / audit)全写
- 重名:不 create checkpoint,不写 row,audit status='failed'
- checkpoint 失败:不写 row,audit status='failed'
- 无 sessionmaker:立即失败,不写任何东西
- 无 checkpoint_manager(测试 / 半装):走通,checkpoint_id=None,row 写入
- manifest 校验:错误 input → audit failed,不进 checkpoint 阶段
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chariot.audit.hooks import AuditHookManager
from chariot.checkpoints import CheckpointManager
from chariot.database.session import dispose_db, init_db
from chariot.repos.audit_repo import AuditRepo
from chariot.repos.skill_repo import SkillRepo
from chariot.skills.propose_service import ProposeInput, SkillProposeService


@pytest_asyncio.fixture
async def sm_path(tmp_path: Path) -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], Path]]:
    db_path = tmp_path / "chariot.db"
    sm = await init_db(db_path)
    try:
        yield sm, db_path
    finally:
        await dispose_db()


def _make_input(name: str = "auto_writer", proposer: str | None = None) -> ProposeInput:
    return ProposeInput(
        name=name,
        description="auto-written skill from propose service test",
        prompt="step 1: do thing\nstep 2: report result\n",
        proposer=proposer,
    )


# ---- 成功路径 ----


async def test_propose_success_writes_row_and_audit_and_checkpoint(
    sm_path: tuple[async_sessionmaker[AsyncSession], Path],
    tmp_path: Path,
) -> None:
    sm, db_path = sm_path
    hooks = AuditHookManager(sm)
    mgr = CheckpointManager(
        sessionmaker=sm,
        db_path=db_path,
        checkpoint_dir=tmp_path / "ckpt",
        config_files=[tmp_path / "nx.yaml"],
        cwd=tmp_path / "not_git",
        audit_hooks=hooks,
    )
    svc = SkillProposeService(sessionmaker=sm, audit_hooks=hooks, checkpoint_manager=mgr)

    result = await svc.propose(_make_input("auto_writer", proposer="ai-agent-x"))
    assert result.ok is True
    assert result.skill_name == "auto_writer"
    assert result.skill_id is not None
    assert result.checkpoint_id is not None

    # row 已落 DB,enabled=False(propose 默认不立即开启)
    async with sm() as session:
        entry = await SkillRepo(session).get_by_name("auto_writer")
        assert entry is not None
        assert entry.enabled is False
        # meta 记 source / proposer / checkpoint_id
        assert entry.meta["source"] == "propose"
        assert entry.meta["proposer"] == "ai-agent-x"
        assert entry.meta["checkpoint_id"] == result.checkpoint_id
        # content 是 YAML 文本
        assert "schema_version" in entry.content
        assert "auto_writer" in entry.content
        # audit 含 skill_store create
        events = await AuditRepo(session).list_events(limit=10)
    types = [(ev.event_type, ev.status) for ev in events]
    assert (AuditHookManager.EVENT_SKILL_STORE, "create") in types


async def test_propose_success_without_checkpoint_manager(
    sm_path: tuple[async_sessionmaker[AsyncSession], Path],
) -> None:
    """无 CheckpointManager(测试 / 半装路径)→ 写 row 但 checkpoint_id=None。"""
    sm, _ = sm_path
    hooks = AuditHookManager(sm)
    svc = SkillProposeService(sessionmaker=sm, audit_hooks=hooks, checkpoint_manager=None)
    result = await svc.propose(_make_input("no_cp"))
    assert result.ok is True
    assert result.checkpoint_id is None
    async with sm() as session:
        entry = await SkillRepo(session).get_by_name("no_cp")
        assert entry is not None
        assert entry.meta["checkpoint_id"] is None


# ---- 失败路径 ----


async def test_propose_duplicate_name_fails(
    sm_path: tuple[async_sessionmaker[AsyncSession], Path],
    tmp_path: Path,
) -> None:
    sm, db_path = sm_path
    hooks = AuditHookManager(sm)
    mgr = CheckpointManager(
        sessionmaker=sm,
        db_path=db_path,
        checkpoint_dir=tmp_path / "ckpt",
        config_files=[tmp_path / "nx.yaml"],
        cwd=tmp_path / "not_git",
        audit_hooks=hooks,
    )
    svc = SkillProposeService(sessionmaker=sm, audit_hooks=hooks, checkpoint_manager=mgr)

    # 先 manual 落一条同名
    async with sm() as session:
        await SkillRepo(session).create(
            name="dupx",
            description="d",
            content="schema_version: 1\nname: dupx\ndescription: d\nprompt: p\n",
            enabled=True,
        )

    result = await svc.propose(_make_input("dupx"))
    assert result.ok is False
    assert result.error is not None
    assert "exists" in result.error.lower() or "already" in result.error.lower()
    # 不应建 checkpoint(重名 check 在 checkpoint 之前)
    assert result.checkpoint_id is None
    # audit 写了 failed
    async with sm() as session:
        events = await AuditRepo(session).list_events(limit=10)
    assert any(ev.event_type == AuditHookManager.EVENT_SKILL_STORE and ev.status == "failed" for ev in events)


async def test_propose_checkpoint_failure_skips_row(
    sm_path: tuple[async_sessionmaker[AsyncSession], Path],
) -> None:
    """checkpoint 失败 → 不写 row。用故意失败的 CheckpointManager.create 模拟。"""
    sm, _ = sm_path
    hooks = AuditHookManager(sm)

    class _BrokenMgr:
        async def create(self, name: str) -> object:
            raise RuntimeError(f"intentional fail: {name}")

    svc = SkillProposeService(sessionmaker=sm, audit_hooks=hooks, checkpoint_manager=_BrokenMgr())  # type: ignore[arg-type]
    result = await svc.propose(_make_input("ghost"))
    assert result.ok is False
    assert result.checkpoint_id is None
    assert result.error is not None
    assert "checkpoint" in result.error.lower()
    # row 不应落
    async with sm() as session:
        entry = await SkillRepo(session).get_by_name("ghost")
        assert entry is None
        events = await AuditRepo(session).list_events(limit=10)
    assert any(ev.event_type == AuditHookManager.EVENT_SKILL_STORE and ev.status == "failed" for ev in events)


async def test_propose_without_sessionmaker_fails_fast() -> None:
    svc = SkillProposeService(sessionmaker=None)
    result = await svc.propose(_make_input("x"))
    assert result.ok is False
    assert "sessionmaker" in (result.error or "")


async def test_propose_invalid_manifest_fails(
    sm_path: tuple[async_sessionmaker[AsyncSession], Path],
) -> None:
    """name 不符 `[a-z][a-z0-9_]*` → SkillLoader 校验拦下,不写 row、不进 checkpoint。"""
    sm, _ = sm_path
    hooks = AuditHookManager(sm)
    svc = SkillProposeService(sessionmaker=sm, audit_hooks=hooks, checkpoint_manager=None)
    bad_input = ProposeInput(
        name="BadCase",  # 大写 → 违反 pattern
        description="something something",
        prompt="prompt body here ok",
    )
    result = await svc.propose(bad_input)
    assert result.ok is False
    assert result.error is not None
    # row 不应落(连 name BadCase 的也没有,因为 manifest 校验拦在前)
    async with sm() as session:
        entry = await SkillRepo(session).get_by_name("BadCase")
        assert entry is None
        events = await AuditRepo(session).list_events(limit=10)
    assert any(ev.event_type == AuditHookManager.EVENT_SKILL_STORE and ev.status == "failed" for ev in events)


# ---- accessor smoke ----


async def test_accessors(sm_path: tuple[async_sessionmaker[AsyncSession], Path]) -> None:
    sm, _ = sm_path
    hooks = AuditHookManager(sm)
    svc = SkillProposeService(sessionmaker=sm, audit_hooks=hooks)
    assert svc.sessionmaker is sm
    assert svc.audit_hooks is hooks
    assert svc.checkpoint_manager is None

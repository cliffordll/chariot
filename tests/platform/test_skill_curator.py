"""B6 wave 4 —— `SkillCurator` 4-bucket 静态分类。

覆盖:
- stale:近 N 天没 activate / 一次都没 activate → 入桶
- underused:总 activate < threshold → 入桶
- failing:最近 window 次里 error 比例 > ratio → 入桶;样本不足不入桶
- overlapping:prompt 相似度 > ratio → 两两入对,按 ratio desc
- empty 路径:registry / activations 都空 → bucket 全空 / stale 含全部 skill
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chariot.audit.hooks import AuditHookManager
from chariot.database.session import dispose_db, init_db
from chariot.repos.audit_repo import AuditRepo
from chariot.skills.base import BuiltinSkill, SkillManifest
from chariot.skills.curator import SkillCurator
from chariot.skills.registry import SkillRegistry


@pytest_asyncio.fixture
async def sm(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    sm = await init_db(tmp_path / "curator.db")
    try:
        yield sm
    finally:
        await dispose_db()


def _skill(name: str, prompt: str = "default prompt body") -> BuiltinSkill:
    return BuiltinSkill(
        SkillManifest(
            schema_version=1,
            name=name,
            version="0.1.0",
            description="d",
            prompt=prompt,
        )
    )


def _registry(skills: list[BuiltinSkill]) -> SkillRegistry:
    return SkillRegistry(builtin={s.name: s for s in skills}, db={})


async def _record_activate(
    sm: async_sessionmaker[AsyncSession],
    *,
    skill_name: str,
    is_error: bool = False,
    created_at: datetime | None = None,
) -> None:
    """直接落 AuditRow,可注入 created_at(走 ORM 默认 now 会全集中,无法测 stale)。"""
    from chariot.database.models import AuditEventRow

    async with sm() as session:
        row = AuditEventRow(
            event_type=AuditHookManager.EVENT_SKILL_ACTIVATE,
            status="error" if is_error else "ok",
            payload=f'{{"skill_name": "{skill_name}", "source": "builtin", '
            f'"is_error": {"true" if is_error else "false"}}}',
        )
        if created_at is not None:
            row.created_at = created_at
        session.add(row)
        await session.commit()


# ---- stale ----


async def test_stale_includes_skills_with_no_activations(sm: async_sessionmaker[AsyncSession]) -> None:
    registry = _registry([_skill("a"), _skill("b")])
    curator = SkillCurator(sessionmaker=sm, skill_registry=registry)
    result = await curator.curate()
    assert sorted(result.stale) == ["a", "b"]


async def test_stale_excludes_recent_activations(sm: async_sessionmaker[AsyncSession]) -> None:
    registry = _registry([_skill("a")])
    # 现在跑一次 activate,a 不应入 stale
    await _record_activate(sm, skill_name="a")
    curator = SkillCurator(sessionmaker=sm, skill_registry=registry)
    result = await curator.curate()
    assert "a" not in result.stale


async def test_stale_includes_old_activations(sm: async_sessionmaker[AsyncSession]) -> None:
    registry = _registry([_skill("a")])
    old = datetime.now(UTC) - timedelta(days=60)
    await _record_activate(sm, skill_name="a", created_at=old)
    curator = SkillCurator(sessionmaker=sm, skill_registry=registry, stale_days=30)
    result = await curator.curate()
    assert "a" in result.stale


# ---- underused ----


async def test_underused_below_threshold(sm: async_sessionmaker[AsyncSession]) -> None:
    registry = _registry([_skill("a"), _skill("b")])
    # a:1 次;b:5 次。threshold=3 → a 入桶,b 不入
    await _record_activate(sm, skill_name="a")
    for _ in range(5):
        await _record_activate(sm, skill_name="b")
    curator = SkillCurator(sessionmaker=sm, skill_registry=registry, underused_threshold=3)
    result = await curator.curate()
    assert "a" in result.underused
    assert "b" not in result.underused


# ---- failing ----


async def test_failing_majority_errors(sm: async_sessionmaker[AsyncSession]) -> None:
    registry = _registry([_skill("a")])
    # 10 次里 7 次 error → ratio=0.7 > 0.5 → failing
    for _ in range(7):
        await _record_activate(sm, skill_name="a", is_error=True)
    for _ in range(3):
        await _record_activate(sm, skill_name="a", is_error=False)
    curator = SkillCurator(sessionmaker=sm, skill_registry=registry, failing_window=10, failing_ratio=0.5)
    result = await curator.curate()
    assert "a" in result.failing


async def test_failing_skipped_when_insufficient_samples(sm: async_sessionmaker[AsyncSession]) -> None:
    """样本数 < window → 不入桶(数据不足)。"""
    registry = _registry([_skill("a")])
    # 只 3 次(window=10),全部 error
    for _ in range(3):
        await _record_activate(sm, skill_name="a", is_error=True)
    curator = SkillCurator(sessionmaker=sm, skill_registry=registry, failing_window=10)
    result = await curator.curate()
    assert "a" not in result.failing


async def test_failing_low_error_ratio_excluded(sm: async_sessionmaker[AsyncSession]) -> None:
    registry = _registry([_skill("a")])
    # 10 次里 2 次 error → ratio=0.2 < 0.5
    for _ in range(2):
        await _record_activate(sm, skill_name="a", is_error=True)
    for _ in range(8):
        await _record_activate(sm, skill_name="a", is_error=False)
    curator = SkillCurator(sessionmaker=sm, skill_registry=registry, failing_window=10, failing_ratio=0.5)
    result = await curator.curate()
    assert "a" not in result.failing


# ---- overlapping ----


async def test_overlapping_detects_similar_prompts(sm: async_sessionmaker[AsyncSession]) -> None:
    base = "be precise and ask before refactoring; explain reasoning carefully"
    similar = "be precise and ask before refactoring; explain reasoning carefully please"
    registry = _registry([_skill("review_a", prompt=base), _skill("review_b", prompt=similar)])
    curator = SkillCurator(sessionmaker=sm, skill_registry=registry, overlap_ratio=0.75)
    result = await curator.curate()
    assert len(result.overlapping) == 1
    a, b, ratio = result.overlapping[0]
    assert (a, b) == ("review_a", "review_b")
    assert ratio > 0.75


async def test_overlapping_ignores_distinct_prompts(sm: async_sessionmaker[AsyncSession]) -> None:
    registry = _registry(
        [
            _skill("a", prompt="please write tests with pytest"),
            _skill("b", prompt="generate documentation in markdown format"),
        ]
    )
    curator = SkillCurator(sessionmaker=sm, skill_registry=registry, overlap_ratio=0.75)
    result = await curator.curate()
    assert result.overlapping == []


# ---- 综合 ----


async def test_curate_returns_all_four_buckets(sm: async_sessionmaker[AsyncSession]) -> None:
    """smoke:跑过 curate 不抛,4 bucket 类型对。"""
    registry = _registry([_skill("a")])
    curator = SkillCurator(sessionmaker=sm, skill_registry=registry)
    result = await curator.curate()
    assert isinstance(result.stale, list)
    assert isinstance(result.underused, list)
    assert isinstance(result.failing, list)
    assert isinstance(result.overlapping, list)


# ---- audit hook 自检(skill_activate 通过 audit_hooks 写后 curator 能读到)----


async def test_curator_reads_from_audit_hooks_path(sm: async_sessionmaker[AsyncSession]) -> None:
    """走 AuditHookManager.record_skill_activate(而非直接落 row)写一条,
    curator 应能读到。"""
    hooks = AuditHookManager(sm)
    await hooks.record_skill_activate(skill_name="a", source="builtin")
    async with sm() as session:
        events = await AuditRepo(session).list_events(limit=10)
    assert any(ev.event_type == AuditHookManager.EVENT_SKILL_ACTIVATE for ev in events)

    registry = _registry([_skill("a")])
    curator = SkillCurator(sessionmaker=sm, skill_registry=registry)
    result = await curator.curate()
    # 一次 activate → 不是 stale(没过 30 天)
    assert "a" not in result.stale

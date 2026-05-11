"""SkillCurator —— 4-bucket 静态分类(B6 wave 4)。

封装策略(CLAUDE.md ⭐):
- 单类编排;模块级零自由函数
- 持 `sessionmaker` + `skill_registry`;读 audit_events + skills.prompt,**不写**
- 4 bucket 互不独占:同一 skill 可被多个 bucket 命中(在 UI 上聚合显示)

Bucket 阈值(经验值;后续 demo 跑通后再调):
- `stale`:30 天没有任何 `skill_activate` audit 事件
- `underused`:总 `skill_activate` 次数 < 3
- `failing`:最近 10 次 `skill_activate` 里 `is_error=True`(payload)比例 > 50%
- `overlapping`:跟其它 skill 的 `prompt` SequenceMatcher.ratio() > 0.75

只 read,**不**自动改 skills.enabled —— 所有建议交人决定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from chariot.audit.hooks import AuditHookManager

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from chariot.skills.registry import SkillRegistry


@dataclass(frozen=True)
class SkillCurateResult:
    """一次 curate 跑出的 4-bucket 结果。每个 bucket 是 skill name 列表(按字母序)。

    `overlapping` 是 `list[tuple[str, str, float]]`,记 (a, b, ratio) —— 两个 skill
    + 相似度;ratio 在 (0.75, 1.0] 之间(>1 不可能)。
    """

    stale: list[str] = field(default_factory=list)
    underused: list[str] = field(default_factory=list)
    failing: list[str] = field(default_factory=list)
    overlapping: list[tuple[str, str, float]] = field(default_factory=list)


class SkillCurator:
    """静态 read-only curator。

    用法::

        curator = SkillCurator(
            sessionmaker=sm,
            skill_registry=registry,
        )
        result = await curator.curate()

    阈值字段(实例化时可覆写,测试用):
    - `stale_days`:默认 30
    - `underused_threshold`:默认 3
    - `failing_window`:默认 10(最近 N 次 activate)
    - `failing_ratio`:默认 0.5
    - `overlap_ratio`:默认 0.75
    """

    DEFAULT_STALE_DAYS = 30
    DEFAULT_UNDERUSED_THRESHOLD = 3
    DEFAULT_FAILING_WINDOW = 10
    DEFAULT_FAILING_RATIO = 0.5
    DEFAULT_OVERLAP_RATIO = 0.75

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        skill_registry: SkillRegistry,
        stale_days: int = DEFAULT_STALE_DAYS,
        underused_threshold: int = DEFAULT_UNDERUSED_THRESHOLD,
        failing_window: int = DEFAULT_FAILING_WINDOW,
        failing_ratio: float = DEFAULT_FAILING_RATIO,
        overlap_ratio: float = DEFAULT_OVERLAP_RATIO,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._skill_registry = skill_registry
        self._stale_days = stale_days
        self._underused_threshold = underused_threshold
        self._failing_window = failing_window
        self._failing_ratio = failing_ratio
        self._overlap_ratio = overlap_ratio

    async def curate(self) -> SkillCurateResult:
        """跑全部 4 bucket。每个 bucket 内部独立 read,不阻其它 bucket。"""
        activations = await self._load_activations()
        skills = self._skill_registry.list_all()
        stale = self._compute_stale(skills, activations)
        underused = self._compute_underused(skills, activations)
        failing = self._compute_failing(skills, activations)
        overlapping = self._compute_overlapping(skills)
        return SkillCurateResult(
            stale=stale,
            underused=underused,
            failing=failing,
            overlapping=overlapping,
        )

    # ---- 数据读 ----

    async def _load_activations(self) -> dict[str, list[tuple[datetime, bool]]]:
        """读 audit_events.skill_activate;返 `skill_name → [(created_at, is_error)]`
        按时间倒序(列表内最近在前)。

        只读最近 30 天 + failing_window 兜底 N 条(查询起来 limit 大一点,client side
        过滤)。避免一次性把整张 audit 表灌进内存。
        """
        from chariot.repos.audit_repo import AuditRepo

        out: dict[str, list[tuple[datetime, bool]]] = {}
        # 读 max(stale_days, failing_window×10) 条,够 4 bucket 算
        limit = max(self._stale_days * 50, self._failing_window * 20, 500)
        async with self._sessionmaker() as session:
            events = await AuditRepo(session).list_events(limit=limit)
        for ev in events:
            if ev.event_type != AuditHookManager.EVENT_SKILL_ACTIVATE:
                continue
            name = ev.payload.get("skill_name")
            if not isinstance(name, str):
                continue
            is_error = bool(ev.payload.get("is_error") or ev.status == "error")
            out.setdefault(name, []).append((ev.created_at, is_error))
        return out

    # ---- bucket 算法 ----

    def _compute_stale(
        self,
        skills: list[object],
        activations: dict[str, list[tuple[datetime, bool]]],
    ) -> list[str]:
        """N 天没被 activate 过(或一次都没)→ stale。"""
        cutoff = self._now() - timedelta(days=self._stale_days)
        result: list[str] = []
        for s in skills:
            name = getattr(s, "name", None)
            if not isinstance(name, str):
                continue
            recs = activations.get(name) or []
            if not recs:
                result.append(name)
                continue
            # recs 是按时间倒序;最新一条 created_at < cutoff → stale
            latest = self._most_recent(recs)
            if latest is None or latest < cutoff:
                result.append(name)
        return sorted(result)

    def _compute_underused(
        self,
        skills: list[object],
        activations: dict[str, list[tuple[datetime, bool]]],
    ) -> list[str]:
        """总 activate 次数 < threshold → underused。"""
        result: list[str] = []
        for s in skills:
            name = getattr(s, "name", None)
            if not isinstance(name, str):
                continue
            count = len(activations.get(name) or [])
            if count < self._underused_threshold:
                result.append(name)
        return sorted(result)

    def _compute_failing(
        self,
        skills: list[object],
        activations: dict[str, list[tuple[datetime, bool]]],
    ) -> list[str]:
        """最近 window 次 activate 里 is_error 比例 > ratio → failing。

        样本数 < window 时不入桶(数据不足,避免噪音)。
        """
        result: list[str] = []
        for s in skills:
            name = getattr(s, "name", None)
            if not isinstance(name, str):
                continue
            recs = activations.get(name) or []
            if len(recs) < self._failing_window:
                continue
            recent = recs[: self._failing_window]
            error_count = sum(1 for _, is_err in recent if is_err)
            ratio = error_count / len(recent)
            if ratio > self._failing_ratio:
                result.append(name)
        return sorted(result)

    def _compute_overlapping(self, skills: list[object]) -> list[tuple[str, str, float]]:
        """两两 prompt 相似度 > ratio。

        O(N²) 跟 skill 数线性 —— builtin 3 条 + 用户提议十几条,N 很小;真大了再
        换 minhash / embedding。返时按 ratio desc。
        """
        items: list[tuple[str, str]] = []  # (name, prompt)
        for s in skills:
            name = getattr(s, "name", None)
            manifest = getattr(s, "manifest", None)
            prompt = getattr(manifest, "prompt", None) if manifest is not None else None
            if not isinstance(name, str) or not isinstance(prompt, str):
                continue
            items.append((name, prompt))

        pairs: list[tuple[str, str, float]] = []
        for i, (name_a, prompt_a) in enumerate(items):
            for name_b, prompt_b in items[i + 1 :]:
                ratio = SequenceMatcher(None, prompt_a, prompt_b).ratio()
                if ratio > self._overlap_ratio:
                    # 字母序排 (a, b),避免 (b, a) 重复
                    if name_a < name_b:
                        pairs.append((name_a, name_b, round(ratio, 3)))
                    else:
                        pairs.append((name_b, name_a, round(ratio, 3)))
        pairs.sort(key=lambda t: -t[2])
        return pairs

    # ---- helpers ----

    @staticmethod
    def _most_recent(recs: list[tuple[datetime, bool]]) -> datetime | None:
        if not recs:
            return None
        return max(t for t, _ in recs)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

"""EvalDiff —— 两个 run snapshot 之间的 verdict 变化对比。

对每个 task:
- **NEW** —— baseline 没有,current 有
- **REMOVED** —— baseline 有,current 没有(task 文件被删了)
- **REGRESSED** —— baseline=PASS,current ∈ {FAIL, ERROR}
- **RECOVERED** —— baseline ∈ {FAIL, ERROR},current=PASS
- **CHANGED** —— 两边都非 PASS 但 verdict 不同(FAIL ↔ ERROR / SKIP 等)
- **STABLE** —— verdict 完全相同

用法:CI / 本地 review 时,只看 REGRESSED + RECOVERED + CHANGED,STABLE 默认隐藏。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from chariot.eval.store import EvalRunSnapshot
from chariot.models.eval import Verdict


class DiffStatus(StrEnum):
    NEW = "NEW"
    REMOVED = "REMOVED"
    REGRESSED = "REGRESSED"
    RECOVERED = "RECOVERED"
    CHANGED = "CHANGED"
    STABLE = "STABLE"


@dataclass(frozen=True)
class DiffEntry:
    task_id: str
    status: DiffStatus
    baseline_verdict: Verdict | None  # None 表示 baseline 里没这个 task
    current_verdict: Verdict | None  # None 表示 current 里没这个 task


@dataclass(frozen=True)
class DiffSummary:
    new: int
    removed: int
    regressed: int
    recovered: int
    changed: int
    stable: int

    @property
    def total_changes(self) -> int:
        return self.new + self.removed + self.regressed + self.recovered + self.changed


class EvalDiff:
    """两个 EvalRunSnapshot 的 verdict 级 diff。无状态;classmethod 风格。"""

    @classmethod
    def compute(cls, *, baseline: EvalRunSnapshot, current: EvalRunSnapshot) -> list[DiffEntry]:
        """按 task_id 全外连接,返按 task_id 排序的 DiffEntry 列表。"""
        base_by_id = {r.task_id: r.verdict for r in baseline.records}
        cur_by_id = {r.task_id: r.verdict for r in current.records}
        all_ids = sorted(set(base_by_id) | set(cur_by_id))
        entries: list[DiffEntry] = []
        for task_id in all_ids:
            b = base_by_id.get(task_id)
            c = cur_by_id.get(task_id)
            entries.append(
                DiffEntry(
                    task_id=task_id,
                    status=cls._classify(b, c),
                    baseline_verdict=b,
                    current_verdict=c,
                )
            )
        return entries

    @classmethod
    def summarize(cls, entries: list[DiffEntry]) -> DiffSummary:
        counts = dict.fromkeys(DiffStatus, 0)
        for e in entries:
            counts[e.status] += 1
        return DiffSummary(
            new=counts[DiffStatus.NEW],
            removed=counts[DiffStatus.REMOVED],
            regressed=counts[DiffStatus.REGRESSED],
            recovered=counts[DiffStatus.RECOVERED],
            changed=counts[DiffStatus.CHANGED],
            stable=counts[DiffStatus.STABLE],
        )

    @classmethod
    def render_lines(cls, entries: list[DiffEntry], *, hide_stable: bool = True) -> list[str]:
        """terminal-friendly 多行 diff 报告;调用方逐行 Renderer.out 输出。"""
        summary = cls.summarize(entries)
        lines: list[str] = []
        lines.append(
            f"diff summary: changes={summary.total_changes}  "
            f"REGRESSED={summary.regressed}  RECOVERED={summary.recovered}  "
            f"NEW={summary.new}  REMOVED={summary.removed}  "
            f"CHANGED={summary.changed}  STABLE={summary.stable}",
        )
        lines.append("")
        for e in entries:
            if hide_stable and e.status is DiffStatus.STABLE:
                continue
            b = e.baseline_verdict.value if e.baseline_verdict is not None else "-"
            c = e.current_verdict.value if e.current_verdict is not None else "-"
            lines.append(f"  [{e.status.value:9s}] {e.task_id}  ({b} → {c})")
        return lines

    @staticmethod
    def _classify(baseline: Verdict | None, current: Verdict | None) -> DiffStatus:
        if baseline is None and current is not None:
            return DiffStatus.NEW
        if baseline is not None and current is None:
            return DiffStatus.REMOVED
        if baseline == current:
            return DiffStatus.STABLE
        # 两个都非 None 且不等
        if baseline is Verdict.PASS:
            return DiffStatus.REGRESSED
        if current is Verdict.PASS:
            return DiffStatus.RECOVERED
        return DiffStatus.CHANGED

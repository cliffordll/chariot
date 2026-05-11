"""EvalDiff.compute / summarize / render_lines。"""

from __future__ import annotations

from pathlib import Path

from chariot.eval.diff import DiffStatus, EvalDiff
from chariot.eval.store import EvalRunSnapshot
from chariot.models.eval import RunRecord, Verdict


def _snap(records_map: dict[str, Verdict], run_id: str = "x") -> EvalRunSnapshot:
    return EvalRunSnapshot(
        run_id=run_id,
        run_dir=Path(),
        meta={},
        tasks=[],
        records=[RunRecord(task_id=tid, verdict=v) for tid, v in records_map.items()],
        summary={},
    )


def test_stable_all_same() -> None:
    base = _snap({"t1": Verdict.PASS, "t2": Verdict.FAIL})
    cur = _snap({"t1": Verdict.PASS, "t2": Verdict.FAIL})
    entries = EvalDiff.compute(baseline=base, current=cur)
    assert all(e.status is DiffStatus.STABLE for e in entries)
    summary = EvalDiff.summarize(entries)
    assert summary.stable == 2
    assert summary.total_changes == 0


def test_regressed_pass_to_fail() -> None:
    base = _snap({"t1": Verdict.PASS})
    cur = _snap({"t1": Verdict.FAIL})
    entries = EvalDiff.compute(baseline=base, current=cur)
    assert entries[0].status is DiffStatus.REGRESSED
    assert EvalDiff.summarize(entries).regressed == 1


def test_regressed_pass_to_error() -> None:
    base = _snap({"t1": Verdict.PASS})
    cur = _snap({"t1": Verdict.ERROR})
    entries = EvalDiff.compute(baseline=base, current=cur)
    assert entries[0].status is DiffStatus.REGRESSED


def test_recovered_fail_to_pass() -> None:
    base = _snap({"t1": Verdict.FAIL})
    cur = _snap({"t1": Verdict.PASS})
    entries = EvalDiff.compute(baseline=base, current=cur)
    assert entries[0].status is DiffStatus.RECOVERED
    assert EvalDiff.summarize(entries).recovered == 1


def test_changed_fail_to_error() -> None:
    """两边都非 PASS,verdict 不同 → CHANGED(不算 regression 也不算 recovery)。"""
    base = _snap({"t1": Verdict.FAIL})
    cur = _snap({"t1": Verdict.ERROR})
    entries = EvalDiff.compute(baseline=base, current=cur)
    assert entries[0].status is DiffStatus.CHANGED
    assert EvalDiff.summarize(entries).changed == 1


def test_new_task_only_in_current() -> None:
    base = _snap({})
    cur = _snap({"t_new": Verdict.PASS})
    entries = EvalDiff.compute(baseline=base, current=cur)
    assert entries[0].status is DiffStatus.NEW
    assert entries[0].baseline_verdict is None
    assert entries[0].current_verdict is Verdict.PASS


def test_removed_task_only_in_baseline() -> None:
    base = _snap({"t_gone": Verdict.PASS})
    cur = _snap({})
    entries = EvalDiff.compute(baseline=base, current=cur)
    assert entries[0].status is DiffStatus.REMOVED
    assert entries[0].current_verdict is None


def test_mixed_summary() -> None:
    base = _snap(
        {
            "stable": Verdict.PASS,
            "regress": Verdict.PASS,
            "recover": Verdict.FAIL,
            "removed": Verdict.PASS,
        }
    )
    cur = _snap(
        {
            "stable": Verdict.PASS,
            "regress": Verdict.FAIL,
            "recover": Verdict.PASS,
            "new": Verdict.PASS,
        }
    )
    entries = EvalDiff.compute(baseline=base, current=cur)
    s = EvalDiff.summarize(entries)
    assert s.stable == 1
    assert s.regressed == 1
    assert s.recovered == 1
    assert s.new == 1
    assert s.removed == 1
    assert s.total_changes == 4  # 不计 stable


def test_render_lines_hides_stable_by_default() -> None:
    base = _snap({"a": Verdict.PASS, "b": Verdict.PASS})
    cur = _snap({"a": Verdict.FAIL, "b": Verdict.PASS})
    entries = EvalDiff.compute(baseline=base, current=cur)
    lines = EvalDiff.render_lines(entries)
    body = "\n".join(lines)
    assert "REGRESSED" in body
    assert " a " in body or "] a" in body
    # b 是 stable,默认隐藏
    assert " b " not in body
    assert "] b" not in body


def test_render_lines_can_show_stable() -> None:
    base = _snap({"a": Verdict.PASS})
    cur = _snap({"a": Verdict.PASS})
    entries = EvalDiff.compute(baseline=base, current=cur)
    lines = EvalDiff.render_lines(entries, hide_stable=False)
    body = "\n".join(lines)
    assert "STABLE" in body

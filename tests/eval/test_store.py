"""EvalRunStore —— save / load / list_runs 端到端。"""

from __future__ import annotations

from pathlib import Path

from chariot.eval.store import EvalRunStore
from chariot.models.eval import GoldenTask, RunRecord, Verdict


def _task(task_id: str = "t1", verifier: str = "exact_match") -> GoldenTask:
    return GoldenTask(
        task_id=task_id,
        prompt="say hi",
        verifier_type=verifier,
        expected={"contains": ["hi"]},
        category="smoke",
    )


def _record(task_id: str = "t1", verdict: Verdict = Verdict.PASS) -> RunRecord:
    return RunRecord(
        task_id=task_id,
        verdict=verdict,
        reason="" if verdict is Verdict.PASS else "missing 'hi'",
        final_response="hi there",
        input_tokens=10,
        output_tokens=20,
        cost_usd=0.001,
        cost_status="estimated",
        duration_seconds=1.5,
        turns=1,
        turn_id="01TURN_FAKE",
        tool_calls=[{"tool_name": "echo_tool", "arguments": {"text": "hi"}, "status": "ok"}],
    )


def test_save_writes_five_files(tmp_path: Path) -> None:
    store = EvalRunStore(root=tmp_path)
    run_id = store.save(tasks=[_task()], records=[_record()], meta={"golden_dir": "tests/golden"})
    run_dir = tmp_path / run_id
    assert run_dir.is_dir()
    for fname in ("meta.json", "tasks.json", "records.json", "summary.json", "report.txt"):
        assert (run_dir / fname).is_file(), f"missing {fname}"


def test_save_meta_includes_extra_fields(tmp_path: Path) -> None:
    import json

    store = EvalRunStore(root=tmp_path)
    run_id = store.save(
        tasks=[_task()],
        records=[_record()],
        meta={"agent_profile": "researcher", "golden_dir": "tests/golden"},
    )
    meta = json.loads((tmp_path / run_id / "meta.json").read_text(encoding="utf-8"))
    assert meta["agent_profile"] == "researcher"
    assert meta["golden_dir"] == "tests/golden"
    assert meta["run_id"] == run_id
    assert "created_at" in meta
    assert meta["schema"] == 1


def test_load_roundtrip(tmp_path: Path) -> None:
    store = EvalRunStore(root=tmp_path)
    tasks = [_task("t1"), _task("t2")]
    records = [_record("t1", Verdict.PASS), _record("t2", Verdict.FAIL)]
    run_id = store.save(tasks=tasks, records=records)
    snap = store.load(run_id)
    assert snap is not None
    assert snap.run_id == run_id
    assert len(snap.tasks) == 2
    assert {t.task_id for t in snap.tasks} == {"t1", "t2"}
    assert len(snap.records) == 2
    assert snap.records[0].verdict is Verdict.PASS
    assert snap.records[1].verdict is Verdict.FAIL
    # 字段细节也要保留
    assert snap.records[0].input_tokens == 10
    assert snap.records[0].tool_calls[0]["tool_name"] == "echo_tool"
    assert snap.summary["total"] == 2
    assert snap.summary["passed"] == 1


def test_list_runs_returns_sorted_desc(tmp_path: Path) -> None:
    """同秒并发会走 -001 / -002 后缀,test 用 mkdir 模拟"""
    (tmp_path / "2026-05-11T10-00-00").mkdir()
    (tmp_path / "2026-05-11T11-00-00").mkdir()
    (tmp_path / "2026-05-11T12-00-00").mkdir()
    store = EvalRunStore(root=tmp_path)
    assert store.list_runs() == [
        "2026-05-11T12-00-00",
        "2026-05-11T11-00-00",
        "2026-05-11T10-00-00",
    ]


def test_list_runs_no_dir_returns_empty(tmp_path: Path) -> None:
    store = EvalRunStore(root=tmp_path / "ghost")
    assert store.list_runs() == []


def test_load_unknown_run_id_returns_none(tmp_path: Path) -> None:
    store = EvalRunStore(root=tmp_path)
    assert store.load("ghost") is None


def test_save_handles_same_second_collision(tmp_path: Path) -> None:
    """同秒 save 两次 → 第二个走 -001 后缀,不报错。"""
    store = EvalRunStore(root=tmp_path)
    id1 = store.save(tasks=[_task()], records=[_record()])
    id2 = store.save(tasks=[_task()], records=[_record()])
    # 时间戳串变化或第二个加了序号
    assert id1 != id2

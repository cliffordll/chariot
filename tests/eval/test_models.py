"""GoldenTask / RunRecord / Verdict / VerifierResult 数据契约。"""

from __future__ import annotations

from chariot.models.eval import GoldenTask, RunRecord, Verdict, VerifierResult


def test_verdict_enum_string_serializes_to_label() -> None:
    """`Verdict` 继承 str 是为了 JSON / 报表直接出 'PASS' / 'FAIL',不出 'Verdict.PASS'。"""
    assert Verdict.PASS == "PASS"
    assert Verdict.FAIL == "FAIL"
    assert Verdict.ERROR == "ERROR"
    assert Verdict.SKIP == "SKIP"
    assert str(Verdict.PASS.value) == "PASS"


def test_golden_task_minimum_required() -> None:
    task = GoldenTask(
        task_id="smoke_oneshot",
        prompt="say hi",
        verifier_type="exact_match",
        expected={"contains": ["hi"]},
    )
    assert task.task_id == "smoke_oneshot"
    assert task.category == "uncategorized"
    assert task.max_iterations == 10
    assert task.model is None
    assert task.system is None


def test_golden_task_full_fields() -> None:
    task = GoldenTask(
        task_id="file_read",
        prompt="read it",
        verifier_type="tool_called",
        expected={"tool": "read_file"},
        category="file",
        description="agent 该用 read_file",
        max_iterations=5,
        model="claude-sonnet-4-6",
        system="custom system",
    )
    assert task.category == "file"
    assert task.max_iterations == 5
    assert task.model == "claude-sonnet-4-6"


def test_run_record_defaults() -> None:
    record = RunRecord(task_id="smoke", verdict=Verdict.PASS)
    assert record.turn_id == ""
    assert record.turns == 0
    assert record.input_tokens == 0
    assert record.output_tokens == 0
    assert record.cost_usd == 0.0
    assert record.cost_status == "unknown"
    assert record.duration_seconds == 0.0
    assert record.tool_calls == []
    assert record.error is None


def test_verifier_result_carries_verdict_and_reason() -> None:
    ok = VerifierResult(verdict=Verdict.PASS)
    bad = VerifierResult(verdict=Verdict.FAIL, reason="missing 'hi' in final_response")
    assert ok.verdict is Verdict.PASS
    assert bad.reason.startswith("missing")

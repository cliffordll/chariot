"""EvalReport —— RunRecord 列表的人类可读渲染。

把 `list[RunRecord]` 压成两层视图:
1. summary —— pass/fail/error/skip 计数 + total tokens + total cost
2. detail —— 单条 task 一行(verdict + task_id + reason + cost)

wave 3 给 CLI 用;wave 4 落盘时复用 `EvalReport.to_dict()` 出 records.json /
summary.json,这样 UI(wave 5)直接读 JSON 渲染。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from chariot.models.eval import RunRecord, Verdict


@dataclass(frozen=True)
class EvalSummary:
    total: int
    passed: int
    failed: int
    errored: int
    skipped: int
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float

    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


class EvalReport:
    """RunRecord 列表 → summary + 行级渲染。无状态;classmethod 都 OK。"""

    @classmethod
    def summarize(cls, records: list[RunRecord]) -> EvalSummary:
        counts = {v: 0 for v in Verdict}
        total_in = total_out = 0
        total_cost = 0.0
        for r in records:
            counts[r.verdict] += 1
            total_in += r.input_tokens
            total_out += r.output_tokens
            total_cost += r.cost_usd
        return EvalSummary(
            total=len(records),
            passed=counts[Verdict.PASS],
            failed=counts[Verdict.FAIL],
            errored=counts[Verdict.ERROR],
            skipped=counts[Verdict.SKIP],
            total_input_tokens=total_in,
            total_output_tokens=total_out,
            total_cost_usd=total_cost,
        )

    @classmethod
    def render_lines(cls, records: list[RunRecord]) -> list[str]:
        """terminal-friendly 多行报告;调用方 Renderer.out 逐行输出。"""
        summary = cls.summarize(records)
        lines: list[str] = []
        lines.append(
            f"summary: {summary.passed}/{summary.total} PASS  "
            f"FAIL={summary.failed}  ERROR={summary.errored}  SKIP={summary.skipped}  "
            f"pass_rate={summary.pass_rate():.0%}",
        )
        lines.append(
            f"tokens: in={summary.total_input_tokens} out={summary.total_output_tokens}  "
            f"cost=${summary.total_cost_usd:.4f}",
        )
        lines.append("")
        for r in records:
            tail = f"  ({r.reason})" if r.reason else ""
            cost = f"  ${r.cost_usd:.4f}" if r.cost_usd > 0 else ""
            lines.append(f"  [{r.verdict.value:5s}] {r.task_id}  turns={r.turns}{cost}{tail}")
        return lines

    @classmethod
    def to_dict(cls, records: list[RunRecord]) -> dict[str, Any]:
        """JSON-serializable 结构,供 wave 4 落盘 `records.json` / `summary.json` 复用。"""
        summary = cls.summarize(records)
        return {
            "summary": {
                "total": summary.total,
                "passed": summary.passed,
                "failed": summary.failed,
                "errored": summary.errored,
                "skipped": summary.skipped,
                "total_input_tokens": summary.total_input_tokens,
                "total_output_tokens": summary.total_output_tokens,
                "total_cost_usd": summary.total_cost_usd,
                "pass_rate": summary.pass_rate(),
            },
            "records": [cls._record_to_dict(r) for r in records],
        }

    @classmethod
    def _record_to_dict(cls, record: RunRecord) -> dict[str, Any]:
        return {
            "task_id": record.task_id,
            "verdict": record.verdict.value,
            "reason": record.reason,
            "turn_id": record.turn_id,
            "turns": record.turns,
            "input_tokens": record.input_tokens,
            "output_tokens": record.output_tokens,
            "cost_usd": record.cost_usd,
            "cost_status": record.cost_status,
            "duration_seconds": record.duration_seconds,
            "final_response": record.final_response,
            "tool_calls": record.tool_calls,
            "error": record.error,
        }

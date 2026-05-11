"""Eval API surface for sidecar inspection (B2 wave 5)。

读路径:
- `list_golden_tasks(golden_dir)`  — 扫 YAML 目录,返 GoldenTask 列表
- `list_eval_runs(runs_dir)`       — 列 `~/.chariot/eval/<run_id>/`,带 summary
- `get_eval_run(run_id, runs_dir)` — 单个 run 完整快照
- `diff_runs(baseline_id, current_id, runs_dir)` — 两 run 的 verdict diff

只读,不接 chariot eval 跑批入口 —— 桌面 UI 触发跑批走 CLI sidecar 已有的
`chat` 等链路过重,B2 wave 5 仍约束在"读已有产物"。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chariot.eval.diff import DiffEntry, DiffStatus, EvalDiff
from chariot.eval.loader import GoldenTaskLoader, GoldenTaskLoadError
from chariot.eval.store import EvalRunSnapshot, EvalRunStore
from chariot.models.eval import GoldenTask, RunRecord
from chariot.rpc.jsonrpc import JsonRpcServer, RpcError
from chariot.sidecar.runtime import SidecarRuntime


class EvalApi:
    def __init__(self, runtime: SidecarRuntime) -> None:
        self._runtime = runtime

    async def list_golden_tasks(self, *, golden_dir: str | None = None) -> dict[str, Any]:
        path = Path(golden_dir) if golden_dir else Path("tests/golden")
        if not path.is_dir():
            return {"tasks": [], "golden_dir": str(path), "missing": True}
        try:
            tasks = GoldenTaskLoader.load_dir(path)
        except GoldenTaskLoadError as exc:
            raise RpcError(JsonRpcServer.ERR_INVALID_PARAMS, str(exc)) from exc
        return {
            "tasks": [self.serialize_task(t) for t in tasks],
            "golden_dir": str(path),
            "missing": False,
        }

    async def list_eval_runs(self, *, runs_dir: str | None = None) -> dict[str, Any]:
        store = self._store(runs_dir)
        run_ids = store.list_runs()
        rows: list[dict[str, Any]] = []
        for run_id in run_ids:
            snap = store.load(run_id)
            if snap is None:
                rows.append({"run_id": run_id, "summary": None, "meta": None})
                continue
            rows.append(
                {
                    "run_id": run_id,
                    "summary": snap.summary,
                    "meta": snap.meta,
                }
            )
        return {"runs": rows, "runs_dir": str(store.root)}

    async def get_eval_run(self, *, run_id: str, runs_dir: str | None = None) -> dict[str, Any]:
        store = self._store(runs_dir)
        snap = store.load(run_id)
        if snap is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"eval run {run_id!r} not found")
        return self.serialize_snapshot(snap)

    async def diff_runs(
        self,
        *,
        baseline_id: str,
        current_id: str,
        runs_dir: str | None = None,
    ) -> dict[str, Any]:
        store = self._store(runs_dir)
        baseline = store.load(baseline_id)
        if baseline is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"baseline run {baseline_id!r} not found")
        current = store.load(current_id)
        if current is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"current run {current_id!r} not found")
        entries = EvalDiff.compute(baseline=baseline, current=current)
        summary = EvalDiff.summarize(entries)
        return {
            "baseline_id": baseline_id,
            "current_id": current_id,
            "entries": [self.serialize_diff_entry(e) for e in entries],
            "summary": {
                "new": summary.new,
                "removed": summary.removed,
                "regressed": summary.regressed,
                "recovered": summary.recovered,
                "changed": summary.changed,
                "stable": summary.stable,
                "total_changes": summary.total_changes,
            },
        }

    # ---- 内部 ----

    @staticmethod
    def _store(runs_dir: str | None) -> EvalRunStore:
        if runs_dir:
            return EvalRunStore(root=Path(runs_dir))
        return EvalRunStore()

    # ---- serializers ----

    @classmethod
    def serialize_snapshot(cls, snap: EvalRunSnapshot) -> dict[str, Any]:
        return {
            "run_id": snap.run_id,
            "run_dir": str(snap.run_dir),
            "meta": snap.meta,
            "summary": snap.summary,
            "tasks": [cls.serialize_task(t) for t in snap.tasks],
            "records": [cls.serialize_record(r) for r in snap.records],
        }

    @staticmethod
    def serialize_task(task: GoldenTask) -> dict[str, Any]:
        return {
            "task_id": task.task_id,
            "prompt": task.prompt,
            "verifier_type": task.verifier_type,
            "expected": task.expected,
            "category": task.category,
            "description": task.description,
            "max_iterations": task.max_iterations,
            "model": task.model,
            "system": task.system,
        }

    @staticmethod
    def serialize_record(record: RunRecord) -> dict[str, Any]:
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

    @staticmethod
    def serialize_diff_entry(entry: DiffEntry) -> dict[str, Any]:
        return {
            "task_id": entry.task_id,
            "status": entry.status.value,
            "baseline_verdict": entry.baseline_verdict.value if entry.baseline_verdict is not None else None,
            "current_verdict": entry.current_verdict.value if entry.current_verdict is not None else None,
        }

    # 暴露 enum 给前端做穷举判断时用
    DIFF_STATUSES: tuple[str, ...] = tuple(s.value for s in DiffStatus)

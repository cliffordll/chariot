"""EvalRunStore —— `~/.chariot/eval/<timestamp>/` 持久化层。

每个 run 一个目录,内含 5 个文件:
- `meta.json`    — 时间戳 / agent_profile / golden_dir / chariot 版本号
- `tasks.json`   — 当时跑的 GoldenTask 列表快照(防 YAML 后续改动让 diff 漂移)
- `records.json` — RunRecord 列表(EvalReport.to_dict 的 records 字段)
- `summary.json` — 统计摘要(EvalReport.to_dict 的 summary 字段)
- `report.txt`   — render_lines 文本报告(人读)

run_id = 目录名 = ISO-like 时间戳(Windows 路径安全:`:` → `-`)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chariot.eval.report import EvalReport
from chariot.models.eval import GoldenTask, RunRecord, Verdict

_DEFAULT_RUNS_DIR = Path.home() / ".chariot" / "eval"


@dataclass(frozen=True)
class EvalRunSnapshot:
    """从磁盘 load 回来的一个 run 的完整快照。"""

    run_id: str
    run_dir: Path
    meta: dict[str, Any]
    tasks: list[GoldenTask]
    records: list[RunRecord]
    summary: dict[str, Any]


class EvalRunStore:
    """读 / 写 `~/.chariot/eval/<run_id>/` 目录。

    无状态;classmethod / staticmethod 风格,根目录通过构造器注入(默认值放
    `~/.chariot/eval/`)。这样 unit 测试可以用 tmp_path 注入,实际 CLI 用默认。
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root if root is not None else _DEFAULT_RUNS_DIR

    def save(
        self,
        *,
        tasks: list[GoldenTask],
        records: list[RunRecord],
        meta: dict[str, Any] | None = None,
    ) -> str:
        """写一个新 run 到 `root/<timestamp>/`,返 run_id(目录名)。

        timestamp 取 UTC 当下,精度到秒;若并发同秒(理论上 B2 串行不会发生)
        加 `-NNN` 序号区分。
        """
        run_id = self._allocate_run_id()
        run_dir = self.root / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        self._write_json(run_dir / "meta.json", self._build_meta(run_id, meta))
        self._write_json(run_dir / "tasks.json", [self._task_to_dict(t) for t in tasks])
        payload = EvalReport.to_dict(records)
        self._write_json(run_dir / "records.json", payload["records"])
        self._write_json(run_dir / "summary.json", payload["summary"])
        (run_dir / "report.txt").write_text("\n".join(EvalReport.render_lines(records)) + "\n", encoding="utf-8")
        return run_id

    def list_runs(self) -> list[str]:
        """返 run_id 列表,按时间倒序(最新在前)。目录不存在 → 空列表。"""
        if not self.root.is_dir():
            return []
        return sorted([p.name for p in self.root.iterdir() if p.is_dir()], reverse=True)

    def load(self, run_id: str) -> EvalRunSnapshot | None:
        """读单个 run 的所有文件 → EvalRunSnapshot;run_id 不存在 → None。"""
        run_dir = self.root / run_id
        if not run_dir.is_dir():
            return None
        meta = self._read_json(run_dir / "meta.json") or {}
        tasks_raw = self._read_json(run_dir / "tasks.json") or []
        records_raw = self._read_json(run_dir / "records.json") or []
        summary = self._read_json(run_dir / "summary.json") or {}
        return EvalRunSnapshot(
            run_id=run_id,
            run_dir=run_dir,
            meta=meta if isinstance(meta, dict) else {},
            tasks=[self._dict_to_task(t) for t in tasks_raw if isinstance(t, dict)],
            records=[self._dict_to_record(r) for r in records_raw if isinstance(r, dict)],
            summary=summary if isinstance(summary, dict) else {},
        )

    # ---- 内部辅助 ----

    def _allocate_run_id(self) -> str:
        base = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S")
        if not (self.root / base).exists():
            return base
        # 同秒并发的极端 fallback:加 -NNN 序号
        for i in range(1, 1000):
            candidate = f"{base}-{i:03d}"
            if not (self.root / candidate).exists():
                return candidate
        raise RuntimeError(f"failed to allocate unique run_id under {self.root}")

    @staticmethod
    def _build_meta(run_id: str, extra: dict[str, Any] | None) -> dict[str, Any]:
        base: dict[str, Any] = {
            "run_id": run_id,
            "created_at": datetime.now(UTC).isoformat(),
            "schema": 1,
        }
        if extra:
            base.update(extra)
        return base

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _read_json(path: Path) -> Any:
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _task_to_dict(task: GoldenTask) -> dict[str, Any]:
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
    def _dict_to_task(raw: dict[str, Any]) -> GoldenTask:
        return GoldenTask(
            task_id=str(raw.get("task_id", "")),
            prompt=str(raw.get("prompt", "")),
            verifier_type=str(raw.get("verifier_type", "")),
            expected=raw.get("expected") or {},
            category=str(raw.get("category", "uncategorized")),
            description=str(raw.get("description", "")),
            max_iterations=int(raw.get("max_iterations", 10)),
            model=raw.get("model"),
            system=raw.get("system"),
        )

    @staticmethod
    def _dict_to_record(raw: dict[str, Any]) -> RunRecord:
        verdict_str = str(raw.get("verdict", "ERROR"))
        try:
            verdict = Verdict(verdict_str)
        except ValueError:
            verdict = Verdict.ERROR
        return RunRecord(
            task_id=str(raw.get("task_id", "")),
            verdict=verdict,
            reason=str(raw.get("reason", "")),
            turn_id=str(raw.get("turn_id", "")),
            turns=int(raw.get("turns", 0)),
            input_tokens=int(raw.get("input_tokens", 0)),
            output_tokens=int(raw.get("output_tokens", 0)),
            cost_usd=float(raw.get("cost_usd", 0.0)),
            cost_status=str(raw.get("cost_status", "unknown")),
            duration_seconds=float(raw.get("duration_seconds", 0.0)),
            final_response=str(raw.get("final_response", "")),
            tool_calls=raw.get("tool_calls") or [],
            error=raw.get("error"),
        )

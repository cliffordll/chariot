"""Evaluation loop(B2)。

把"任何改动都有数字"装到 chariot:跑 golden task → RunRecord → verdict
(PASS / FAIL / ERROR / SKIP)。落 baseline,后续 diff 看回归。

模块边界:
- `models`        — 纯数据(GoldenTask / RunRecord / VerifierResult / Verdict)
- `loader`        — YAML → GoldenTask
- `verifiers/`    — 四种 verifier(exact_match / tool_called / file_state / output_schema)
- `cost`          — 从 B1 trace 表反查 token / cost / tool_calls 填进 RunRecord
- `runner`        — 编排:AgentFactory 注入 + 跑 task + dispatch verifier
- `report`        — RunRecord 列表 → summary + 行渲染 + JSON dict(wave 4 落盘复用)

不在这层做的:
- 真 AIAgent 实例化(由 CLI 注入,pytest 注入 stub)
- 持久化(`~/.chariot/eval/<timestamp>/`,wave 4 加)
- 桌面 Evals 页(wave 5 加)

注:本模块取代了 0.7.0 phase 4 留下的 `chariot.eval`(只 re-export `EvalRepo`)
shim。`EvalRepo` / `EvalRunEntry` / `EvalCaseEntry` 现在直接从 `chariot.repos.eval_repo`
import,跟新 eval 入口分层。
"""

from chariot.eval.cost import TraceCostExtractor
from chariot.eval.diff import DiffEntry, DiffStatus, DiffSummary, EvalDiff
from chariot.eval.loader import GoldenTaskLoader
from chariot.eval.report import EvalReport, EvalSummary
from chariot.eval.runner import AgentFactory, EvalRunner
from chariot.eval.store import EvalRunSnapshot, EvalRunStore
from chariot.eval.verifiers import VERIFIERS, BaseVerifier
from chariot.models.eval import GoldenTask, RunRecord, Verdict, VerifierResult

__all__ = [
    "VERIFIERS",
    "AgentFactory",
    "BaseVerifier",
    "DiffEntry",
    "DiffStatus",
    "DiffSummary",
    "EvalDiff",
    "EvalReport",
    "EvalRunSnapshot",
    "EvalRunStore",
    "EvalRunner",
    "EvalSummary",
    "GoldenTask",
    "GoldenTaskLoader",
    "RunRecord",
    "TraceCostExtractor",
    "Verdict",
    "VerifierResult",
]

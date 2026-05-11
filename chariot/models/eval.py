"""B2 eval 数据模型 —— 纯 dataclass,无行为。

四类对象:
- `Verdict`        — 4 态枚举(PASS / FAIL / ERROR / SKIP)
- `GoldenTask`     — YAML 文件加载后的 task 定义
- `VerifierResult` — verifier 的判定输出(verdict + reason)
- `RunRecord`      — 一次 task 跑完后的完整记录(关联 trace_turns.id)

跟 `chariot/models/trace.py` 同分法:数据模型住 model 层,Runner / Loader /
Verifier 等运行时类住 `chariot/eval/`(类比 `chariot/trace/writer.py`)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Verdict(StrEnum):
    """task 跑完 + verifier 判定后的最终态。

    `StrEnum`(Python 3.11+)是为了 JSON 序列化和报表渲染直接出 'PASS' / 'FAIL'
    / 'ERROR' / 'SKIP' 而不是 `Verdict.PASS`。
    """

    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    SKIP = "SKIP"


@dataclass(frozen=True)
class GoldenTask:
    """单个 golden task(由 YAML 加载)。

    字段对齐 evolution-design.md §5.2 schema。`expected` 是 verifier-specific
    payload,verifier 内部解析(避免 GoldenTask 提前 typed —— 不同 verifier
    需要的字段不同)。
    """

    task_id: str
    prompt: str
    verifier_type: str  # exact_match / tool_called / file_state / output_schema
    expected: dict[str, Any]
    category: str = "uncategorized"
    description: str = ""
    max_iterations: int = 10
    model: str | None = None  # None = 用 agent 默认 provider
    system: str | None = None  # None = 用 agent_profile / active bundle


@dataclass(frozen=True)
class VerifierResult:
    """verifier 对一次 run 的判定结果。"""

    verdict: Verdict
    reason: str = ""


@dataclass
class RunRecord:
    """跑完一个 golden task 后的完整记录。

    `turn_id` 是关键的 B1 关联点 —— verifier 跑完后,所有 token / cost /
    tool_calls / final_response 都能从 trace_turns + trace_tool_calls 反查。
    Runner 在 task 跑完后会从 trace 表填这些字段,不重复保存全量 wire payload。
    """

    task_id: str
    verdict: Verdict
    reason: str = ""
    turn_id: str = ""  # 关联 trace_turns.id;空表示没跑到 turn(early skip / error)
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cost_status: str = "unknown"  # estimated / actual / included / unknown
    duration_seconds: float = 0.0
    final_response: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)  # 从 trace_tool_calls 拼
    error: str | None = None

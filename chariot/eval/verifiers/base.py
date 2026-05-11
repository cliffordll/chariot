"""BaseVerifier ABC —— verifier 子类的契约。

verifier 的输入:
- `task`   — GoldenTask 定义(主要拿 `expected` payload)
- `record` — Runner 跑完 task 后填好的 RunRecord(turn_id / final_response /
             tool_calls / token / cost / duration 已就绪)

verifier 的输出:VerifierResult(verdict + reason)。

verifier 不动 RunRecord 本身,只判定;Runner 拿到 VerifierResult 后把
verdict / reason copy 进 record。这条边界让 verifier 保持无状态、可单独测。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from chariot.models.eval import GoldenTask, RunRecord, VerifierResult


class BaseVerifier(ABC):
    """判定一次 RunRecord 是否满足 GoldenTask 的 expected。"""

    @abstractmethod
    def verify(self, task: GoldenTask, record: RunRecord) -> VerifierResult:
        """返判定结果。无副作用,不抛 —— 任何错都转 VerifierResult(ERROR, reason)。"""

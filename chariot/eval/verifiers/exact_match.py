"""ExactMatchVerifier —— final_response 必须全部包含指定子串(AND)。

`expected` schema:
- `contains: list[str]` —— 必填;每条子串都要在 final_response 里出现一次
- `case_insensitive: bool` —— 可选;默认 false

不做正则、不做 fuzzy 匹配 —— 故意保守。Eval verifier 只判"输出是否含某些关键
事实",不判"措辞美不美";后者交给 reflection / critic(B4)。
"""

from __future__ import annotations

from chariot.eval.verifiers.base import BaseVerifier
from chariot.models.eval import GoldenTask, RunRecord, Verdict, VerifierResult


class ExactMatchVerifier(BaseVerifier):
    def verify(self, task: GoldenTask, record: RunRecord) -> VerifierResult:
        contains = task.expected.get("contains")
        if not isinstance(contains, list) or not contains:
            return VerifierResult(
                verdict=Verdict.ERROR,
                reason="exact_match: 'expected.contains' must be a non-empty list of strings",
            )
        case_insensitive = bool(task.expected.get("case_insensitive", False))
        response = record.final_response or ""
        haystack = response.lower() if case_insensitive else response
        missing: list[str] = []
        for needle in contains:
            if not isinstance(needle, str):
                return VerifierResult(
                    verdict=Verdict.ERROR,
                    reason=f"exact_match: 'contains' entries must be strings, got {type(needle).__name__}",
                )
            check = needle.lower() if case_insensitive else needle
            if check not in haystack:
                missing.append(needle)
        if missing:
            return VerifierResult(
                verdict=Verdict.FAIL,
                reason=f"missing {missing!r} in final_response (case_insensitive={case_insensitive})",
            )
        return VerifierResult(verdict=Verdict.PASS)

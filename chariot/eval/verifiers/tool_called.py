"""ToolCalledVerifier —— tool_calls 至少一项 name + args 匹配。

`expected` schema:
- `tool: str` —— 必填;tool_name 必须严格相等
- `args_subset: dict[str, Any]` —— 可选;若提供,要求 tool_call.arguments 是
  expected args_subset 的 superset(每个 expected k 都存在且 value 严格相等)

`args_subset` 而非 `args_exact` 是有意:LLM 往往会传些 verbose extra args
(`limit=10` 之类),不该因 extra 就 FAIL;真正想测的是"path / url 这种关键
参数有没有传对"。
"""

from __future__ import annotations

from typing import Any

from chariot.eval.verifiers.base import BaseVerifier
from chariot.models.eval import GoldenTask, RunRecord, Verdict, VerifierResult


class ToolCalledVerifier(BaseVerifier):
    def verify(self, task: GoldenTask, record: RunRecord) -> VerifierResult:
        expected_tool = task.expected.get("tool")
        if not isinstance(expected_tool, str) or not expected_tool:
            return VerifierResult(
                verdict=Verdict.ERROR,
                reason="tool_called: 'expected.tool' must be a non-empty string",
            )
        args_subset = task.expected.get("args_subset")
        if args_subset is not None and not isinstance(args_subset, dict):
            return VerifierResult(
                verdict=Verdict.ERROR,
                reason="tool_called: 'args_subset' must be a mapping (or omitted)",
            )

        if not record.tool_calls:
            return VerifierResult(
                verdict=Verdict.FAIL,
                reason=f"no tool calls in run (expected {expected_tool!r})",
            )

        seen_tools: list[str] = []
        for call in record.tool_calls:
            name = call.get("tool_name") or call.get("name")
            if not isinstance(name, str):
                continue
            seen_tools.append(name)
            if name != expected_tool:
                continue
            if args_subset is None:
                return VerifierResult(verdict=Verdict.PASS)
            args = call.get("arguments") or call.get("args") or {}
            if not isinstance(args, dict):
                continue
            if self._args_match(args, args_subset):
                return VerifierResult(verdict=Verdict.PASS)
        if expected_tool not in seen_tools:
            return VerifierResult(
                verdict=Verdict.FAIL,
                reason=f"expected tool {expected_tool!r} not called; got {seen_tools}",
            )
        return VerifierResult(
            verdict=Verdict.FAIL,
            reason=f"{expected_tool!r} was called but args didn't match {args_subset!r}",
        )

    @staticmethod
    def _args_match(actual: dict[str, Any], expected_subset: dict[str, Any]) -> bool:
        """actual 必须是 expected_subset 的 superset:每个 expected k 都存在且 value 严格相等。"""
        return all(actual.get(key) == want for key, want in expected_subset.items())

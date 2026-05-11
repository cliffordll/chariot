"""sidecar guardrail RPC adapters(B5 wave 1)。

只读 + dry-run:
- `list_guardrails`:列规则 + 当日配额
- `try_guardrail`:dry-run 评估(不消耗配额)
"""

from __future__ import annotations

from typing import Any

from chariot.rpc.jsonrpc import JsonRpcServer, RpcContext, RpcError
from chariot.sidecar.methods import MethodBase


class GuardrailMethods(MethodBase):
    async def list_(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del params, ctx
        engine = self.agent.guardrail_engine
        if engine is None:
            return {"rules": []}
        rules = []
        for rule in engine.rules:
            rem = engine.quota.peek_remaining(rule.rule_id, quota=rule.daily_quota)
            rules.append(
                {
                    "rule_id": rule.rule_id,
                    "verdict": rule.verdict.value,
                    "description": rule.description,
                    "daily_quota": rule.daily_quota,
                    "quota_remaining": rem,
                }
            )
        return {"rules": rules}

    async def try_(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        tool_name = self._require_str(params, "tool_name")
        args = self._optional_dict(params, "args") or {}
        engine = self.agent.guardrail_engine
        if engine is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, "no guardrail engine loaded")
        verdict = engine.preview(tool_name=tool_name, args=args)
        return {
            "rule_id": verdict.rule_id,
            "verdict": verdict.verdict.value,
            "reason": verdict.reason,
            "matched_pattern": verdict.matched_pattern,
            "quota_remaining": verdict.quota_remaining,
            "quota_exhausted": verdict.quota_exhausted,
        }

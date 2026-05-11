"""GuardrailEngine —— 规则编排 + 配额 dispatch(B5 wave 1)。

封装策略(CLAUDE.md ⭐):
- 所有逻辑收进 `GuardrailEngine` 类;模块级零自由函数(除一个标准 sentinel
  常量 `_DEFAULT_ALLOW_RULE_ID`)
- 装 N 个 `BaseRule` 实例 + 一个 `DailyQuotaTracker`,evaluate 走全规则按
  verdict 优先级取最严
- 决策优先级:DENY > REQUIRE_APPROVAL > ALLOW;配额超限把 REQUIRE_APPROVAL
  自动降级 DENY
"""

from __future__ import annotations

from typing import Any, Self

from chariot.guardrails.base import BaseRule, GuardrailVerdict, Verdict
from chariot.guardrails.quota import DailyQuotaTracker

_DEFAULT_ALLOW_RULE_ID = "_default"
"""无任何规则命中时的占位 rule_id。"""


class GuardrailEngine:
    """规则集合 + 评估。

    用法::

        engine = GuardrailEngine.with_defaults()
        verdict = engine.evaluate(tool_name="shell_exec", args={"command": "rm -rf /tmp/x"})
        # verdict.verdict == Verdict.DENY,rule_id='shell_rm_rf'
    """

    def __init__(self, *, rules: list[BaseRule] | None = None) -> None:
        self._rules = list(rules) if rules is not None else []
        self._quota = DailyQuotaTracker()

    @classmethod
    def with_defaults(cls) -> Self:
        """装 13 个内置规则(B5 wave 1 集合)。"""
        from chariot.guardrails.builtin import default_rules

        return cls(rules=default_rules())

    @property
    def rules(self) -> list[BaseRule]:
        return list(self._rules)

    @property
    def quota(self) -> DailyQuotaTracker:
        return self._quota

    def evaluate(self, *, tool_name: str, args: dict[str, Any]) -> GuardrailVerdict:
        """跑所有规则,按 verdict 优先级取最严的命中。"""
        hits: list[tuple[BaseRule, str]] = []
        for rule in self._rules:
            matched = rule.matches(tool_name, args)
            if matched is not None:
                hits.append((rule, matched))
        if not hits:
            return GuardrailVerdict(
                rule_id=_DEFAULT_ALLOW_RULE_ID,
                verdict=Verdict.ALLOW,
                reason="no rule matched",
            )
        # 优先级:DENY > REQUIRE_APPROVAL > ALLOW(虽然 ALLOW 规则当前不存在)
        priority = {Verdict.DENY: 2, Verdict.REQUIRE_APPROVAL: 1, Verdict.ALLOW: 0}
        hits.sort(key=lambda hr: priority[hr[0].verdict], reverse=True)
        rule, matched = hits[0]
        return self._build_verdict(rule, matched, consume_quota=True)

    def preview(self, *, tool_name: str, args: dict[str, Any]) -> GuardrailVerdict:
        """跟 `evaluate` 一致的判定逻辑,但**不消耗配额**(给 `chariot guardrail try` 用)。"""
        hits: list[tuple[BaseRule, str]] = []
        for rule in self._rules:
            matched = rule.matches(tool_name, args)
            if matched is not None:
                hits.append((rule, matched))
        if not hits:
            return GuardrailVerdict(
                rule_id=_DEFAULT_ALLOW_RULE_ID,
                verdict=Verdict.ALLOW,
                reason="no rule matched",
            )
        priority = {Verdict.DENY: 2, Verdict.REQUIRE_APPROVAL: 1, Verdict.ALLOW: 0}
        hits.sort(key=lambda hr: priority[hr[0].verdict], reverse=True)
        rule, matched = hits[0]
        return self._build_verdict(rule, matched, consume_quota=False)

    # ---- 内部 ----

    def _build_verdict(
        self,
        rule: BaseRule,
        matched: str,
        *,
        consume_quota: bool,
    ) -> GuardrailVerdict:
        """走 quota 决定最终 verdict + 拼解释文案。"""
        # DENY 类不走配额(配额对 ALLOW / REQUIRE_APPROVAL 才有意义)
        if rule.verdict == Verdict.DENY:
            return GuardrailVerdict(
                rule_id=rule.rule_id,
                verdict=Verdict.DENY,
                reason=f"{rule.description}(命中 {matched!r})",
                matched_pattern=matched,
                quota_remaining=None,
            )

        remaining_before = self._quota.peek_remaining(rule.rule_id, quota=rule.daily_quota)

        # REQUIRE_APPROVAL 类:配额已满 → 自动降级 DENY
        if rule.daily_quota is not None and remaining_before is not None and remaining_before <= 0:
            return GuardrailVerdict(
                rule_id=rule.rule_id,
                verdict=Verdict.DENY,
                reason=f"{rule.description};今日配额已满({rule.daily_quota}/天)→ 自动降级 DENY",
                matched_pattern=matched,
                quota_remaining=0,
                quota_exhausted=True,
            )

        # 还在配额内:发出 REQUIRE_APPROVAL,evaluate 模式消耗一次配额
        if consume_quota:
            self._quota.try_consume(rule.rule_id, quota=rule.daily_quota)
            remaining_after = self._quota.peek_remaining(rule.rule_id, quota=rule.daily_quota)
        else:
            remaining_after = remaining_before
        return GuardrailVerdict(
            rule_id=rule.rule_id,
            verdict=rule.verdict,
            reason=rule.description,
            matched_pattern=matched,
            quota_remaining=remaining_after,
        )

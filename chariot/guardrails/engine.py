"""GuardrailEngine —— 规则编排 + 配额 dispatch(B5 wave 1)+ capability gate(wave 3)。

封装策略(CLAUDE.md ⭐):
- 所有逻辑收进 `GuardrailEngine` 类;模块级零自由函数(除一个标准 sentinel
  常量 `_DEFAULT_ALLOW_RULE_ID`)
- 装 N 个 `BaseRule` 实例 + 一个 `DailyQuotaTracker`,evaluate 走全规则按
  verdict 优先级取最严
- 决策优先级:DENY > REQUIRE_APPROVAL > ALLOW;配额超限把 REQUIRE_APPROVAL
  自动降级 DENY
- B5 wave 3:可选挂 `Capabilities`,规则若声明 `capability_gate` 且对应 cap
  enabled,默认 DENY → REQUIRE_APPROVAL(只在引擎统一处理,规则本身不动)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self

from chariot.guardrails.base import BaseRule, GuardrailVerdict, Verdict
from chariot.guardrails.quota import DailyQuotaTracker

if TYPE_CHECKING:
    from chariot.agent.config import Capabilities

_DEFAULT_ALLOW_RULE_ID = "_default"
"""无任何规则命中时的占位 rule_id。"""


class GuardrailEngine:
    """规则集合 + 评估。

    用法::

        engine = GuardrailEngine.with_defaults()
        verdict = engine.evaluate(tool_name="shell_exec", args={"command": "rm -rf /tmp/x"})
        # verdict.verdict == Verdict.DENY,rule_id='shell_rm_rf'

    B5 wave 3:可选挂 `Capabilities`,规则中声明的 `capability_gate` 在
    enable=True 时把 DENY 降级为 REQUIRE_APPROVAL。
    """

    def __init__(
        self,
        *,
        rules: list[BaseRule] | None = None,
        capabilities: Capabilities | None = None,
    ) -> None:
        self._rules = list(rules) if rules is not None else []
        self._quota = DailyQuotaTracker()
        self._capabilities = capabilities

    @classmethod
    def with_defaults(cls, *, capabilities: Capabilities | None = None) -> Self:
        """装 13 个内置规则(B5 wave 1 集合)+ 可选 capabilities(wave 3)。"""
        from chariot.guardrails.builtin import default_rules

        return cls(rules=default_rules(), capabilities=capabilities)

    @property
    def rules(self) -> list[BaseRule]:
        return list(self._rules)

    @property
    def quota(self) -> DailyQuotaTracker:
        return self._quota

    @property
    def capabilities(self) -> Capabilities | None:
        return self._capabilities

    def set_capabilities(self, capabilities: Capabilities | None) -> None:
        """运行时切 capability(CLI `--yolo` 全局 flag / DB `chariot capability enable`
        后 sidecar reload 走的路径)。"""
        self._capabilities = capabilities

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

    def _effective_verdict(self, rule: BaseRule) -> Verdict:
        """规则的"运行时 verdict" —— ClassVar 默认值 + capability gate 翻转。

        约定:规则的 `capability_gate` 非 None 且对应 cap enabled 时,DENY 升级
        为 REQUIRE_APPROVAL(让 ApprovalPolicy 决定放/拒)。其它情况不动。
        """
        if self._capabilities is None or rule.capability_gate is None or rule.verdict != Verdict.DENY:
            return rule.verdict
        cap_enabled = getattr(self._capabilities, rule.capability_gate, False)
        if cap_enabled:
            return Verdict.REQUIRE_APPROVAL
        return rule.verdict

    def _build_verdict(
        self,
        rule: BaseRule,
        matched: str,
        *,
        consume_quota: bool,
    ) -> GuardrailVerdict:
        """走 quota 决定最终 verdict + 拼解释文案。"""
        effective = self._effective_verdict(rule)
        # DENY 类不走配额(配额对 ALLOW / REQUIRE_APPROVAL 才有意义)
        if effective == Verdict.DENY:
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

        # 还在配额内:发出 effective verdict,evaluate 模式消耗一次配额
        if consume_quota:
            self._quota.try_consume(rule.rule_id, quota=rule.daily_quota)
            remaining_after = self._quota.peek_remaining(rule.rule_id, quota=rule.daily_quota)
        else:
            remaining_after = remaining_before
        return GuardrailVerdict(
            rule_id=rule.rule_id,
            verdict=effective,
            reason=rule.description,
            matched_pattern=matched,
            quota_remaining=remaining_after,
        )

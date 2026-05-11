"""B5 wave 1 — `GuardrailEngine` dispatch + 配额 + priority + preview。"""

from __future__ import annotations

from chariot.guardrails import GuardrailEngine, Verdict
from chariot.guardrails.builtin import ShellChmodUnsafeRule, ShellRmRfRule

# ---- 默认实例 + 优先级 ----


def test_with_defaults_has_13_rules() -> None:
    engine = GuardrailEngine.with_defaults()
    assert len(engine.rules) == 13


def test_evaluate_no_match_returns_allow() -> None:
    engine = GuardrailEngine.with_defaults()
    v = engine.evaluate(tool_name="read_file", args={"path": "README.md"})
    assert v.verdict == Verdict.ALLOW
    assert v.rule_id == "_default"


def test_evaluate_deny_short_circuits() -> None:
    engine = GuardrailEngine.with_defaults()
    v = engine.evaluate(tool_name="shell_exec", args={"command": "rm -rf /tmp/x"})
    assert v.verdict == Verdict.DENY
    assert v.rule_id == "shell_rm_rf"
    assert v.matched_pattern is not None and "rm -rf" in v.matched_pattern


def test_evaluate_require_approval_returns_proper_verdict() -> None:
    engine = GuardrailEngine.with_defaults()
    v = engine.evaluate(tool_name="shell_exec", args={"command": "chmod 777 /opt"})
    assert v.verdict == Verdict.REQUIRE_APPROVAL
    assert v.rule_id == "shell_chmod_unsafe"
    # 默认配额 5,消耗 1 后剩 4
    assert v.quota_remaining == 4


def test_evaluate_deny_wins_over_require_approval() -> None:
    """同一调用同时命中 DENY + REQUIRE_APPROVAL → 取 DENY(只构造一个能两边命中的)。

    其实跨规则共存的命中很罕见,这里用人工最小验证:
    把两类规则都装上,模拟同时命中。
    """
    rm = ShellRmRfRule()
    chmod = ShellChmodUnsafeRule()
    engine = GuardrailEngine(rules=[chmod, rm])  # 故意把 REQUIRE_APPROVAL 排前
    v = engine.evaluate(
        tool_name="shell_exec",
        args={"command": "chmod 777 /opt && rm -rf /tmp"},
    )
    assert v.verdict == Verdict.DENY
    assert v.rule_id == "shell_rm_rf"


# ---- 配额机制 ----


def test_quota_consumed_on_evaluate() -> None:
    engine = GuardrailEngine(rules=[ShellChmodUnsafeRule()])
    # daily_quota=5;消耗 5 次后第 6 次应该自动降级 DENY
    for i in range(5):
        v = engine.evaluate(tool_name="shell_exec", args={"command": "chmod 777 /opt"})
        assert v.verdict == Verdict.REQUIRE_APPROVAL
        assert v.quota_remaining == 5 - (i + 1)
    # 第 6 次:配额满 → 降级 DENY
    v6 = engine.evaluate(tool_name="shell_exec", args={"command": "chmod 777 /opt"})
    assert v6.verdict == Verdict.DENY
    assert v6.quota_exhausted is True
    assert v6.quota_remaining == 0


def test_preview_does_not_consume_quota() -> None:
    engine = GuardrailEngine(rules=[ShellChmodUnsafeRule()])
    # preview 5 次,配额仍是 5
    for _ in range(5):
        v = engine.preview(tool_name="shell_exec", args={"command": "chmod 777 /opt"})
        assert v.verdict == Verdict.REQUIRE_APPROVAL
        # remaining 永远是 5(还没消耗)
        assert v.quota_remaining == 5
    # 之后 evaluate 一次,配额开始减
    v_eval = engine.evaluate(tool_name="shell_exec", args={"command": "chmod 777 /opt"})
    assert v_eval.quota_remaining == 4


def test_deny_rule_does_not_consume_quota() -> None:
    """DENY 类无配额,evaluate 多次不影响其它规则配额。"""
    engine = GuardrailEngine.with_defaults()
    for _ in range(20):
        v = engine.evaluate(tool_name="shell_exec", args={"command": "rm -rf /tmp"})
        assert v.verdict == Verdict.DENY
        assert v.quota_remaining is None


# ---- preview / evaluate 一致(verdict 一致但配额计数差) ----


def test_preview_and_evaluate_agree_on_verdict() -> None:
    engine = GuardrailEngine.with_defaults()
    cases = [
        ("shell_exec", {"command": "rm -rf /"}, Verdict.DENY),
        ("shell_exec", {"command": "chmod 777 /a"}, Verdict.REQUIRE_APPROVAL),
        ("read_file", {"path": "x"}, Verdict.ALLOW),
        ("write_file", {"path": "chariot/foo.py"}, Verdict.DENY),
        ("write_file", {"path": ".env"}, Verdict.REQUIRE_APPROVAL),
    ]
    for tool, args, expected in cases:
        pv = engine.preview(tool_name=tool, args=args)
        ev = engine.evaluate(tool_name=tool, args=args)
        assert pv.verdict == expected, f"preview {tool} {args}"
        assert ev.verdict == expected, f"evaluate {tool} {args}"


def test_empty_engine_always_allows() -> None:
    engine = GuardrailEngine(rules=[])
    v = engine.evaluate(tool_name="shell_exec", args={"command": "rm -rf /"})
    assert v.verdict == Verdict.ALLOW
    assert v.rule_id == "_default"

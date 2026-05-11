"""Guardrails 子包(B5 wave 1)。

工具调用拦截层 —— 13 条内置规则盯 `shell_exec` / `write_file` / 等工具的
参数,危险动作(`rm -rf` / `chmod 777` / `git push --force` / DB drop / ...)
直接 DENY 或 REQUIRE_APPROVAL。

API:
- `GuardrailEngine.with_defaults()` 拿一个装好 13 内置规则的引擎
- `engine.evaluate(tool_name=..., args=...)` 跑评估,返 `GuardrailVerdict`
- `engine.preview(...)`:同 evaluate 但不消耗配额(给 `chariot guardrail try` 用)

封装:`BaseRule` ABC + `Verdict` Literal + `GuardrailVerdict` frozen dataclass;
模块级零自由函数(对照 CLAUDE.md ⭐ 封装与内聚最高优先级)。
"""

from chariot.guardrails.base import BaseRule, GuardrailVerdict, Verdict
from chariot.guardrails.engine import GuardrailEngine
from chariot.guardrails.quota import DailyQuotaTracker

__all__ = [
    "BaseRule",
    "DailyQuotaTracker",
    "GuardrailEngine",
    "GuardrailVerdict",
    "Verdict",
]

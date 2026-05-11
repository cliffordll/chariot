"""Guardrails ABC + verdict 数据对象(B5 wave 1)。

拦截层契约:
- 每个内置规则 = 一个 `BaseRule` 子类,带 `rule_id` / 默认 verdict / 可选日配额
- `matches(tool_name, args)` 返 matched pattern 字符串(命中) / None(未命中)
- `GuardrailEngine.evaluate` 走所有规则,优先 DENY > REQUIRE_APPROVAL > ALLOW

封装策略(CLAUDE.md ⭐):
- ABC + 子类 dispatch,不写 if/elif 长链
- 每个规则文件独立(便于阅读 + 单测),`builtin/` 子目录承载
- Verdict / GuardrailVerdict frozen dataclass / Literal,跨层共享
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar


class Verdict(StrEnum):
    """三档裁决。"""

    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True)
class GuardrailVerdict:
    """一次评估结果。

    - `rule_id`:命中的规则 id(`_default` = 全部未命中走 ALLOW)
    - `verdict`:三档之一
    - `reason`:自然语言描述(给 audit / surface 显示)
    - `matched_pattern`:命中的具体 regex(给 audit 留证;ALLOW / 无命中时为 None)
    - `quota_remaining`:命中规则今日剩余配额(None = 不限);可帮 surface 提示
    - `quota_exhausted`:True 表示配额已超 → REQUIRE_APPROVAL 自动降级为 DENY
    """

    rule_id: str
    verdict: Verdict
    reason: str
    matched_pattern: str | None = None
    quota_remaining: int | None = None
    quota_exhausted: bool = False


class BaseRule(ABC):
    """单个 guardrail 规则的契约。

    子类 ClassVar:
    - `rule_id`:全局唯一 id(`shell_rm_rf` / `git_force_push` 等)
    - `description`:一句话描述(给 `chariot guardrail list` 展示)
    - `verdict`:命中后的默认 verdict
    - `daily_quota`:日配额上限(None = 不限;数字 = 每天最多放过几次)
    """

    rule_id: ClassVar[str]
    description: ClassVar[str]
    verdict: ClassVar[Verdict]
    daily_quota: ClassVar[int | None] = None

    @abstractmethod
    def matches(self, tool_name: str, args: dict[str, Any]) -> str | None:
        """命中返 matched_pattern(给 audit 留证;通常是命中的具体 regex 或 arg 值);
        未命中返 None。"""

"""HTTP 写动作(POST / PUT / DELETE / PATCH)默认要审批(B5 wave 1,REQUIRE_APPROVAL)。

`http_get` 工具默认 GET 安全;若未来加 `http_post` / 通用 HTTP tool,把
method='POST/PUT/DELETE/PATCH' 当写动作拦截。
"""

from __future__ import annotations

from typing import Any

from chariot.guardrails.base import BaseRule, Verdict

_WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}


class HttpPostUnsafeRule(BaseRule):
    rule_id = "http_post_unsafe"
    description = "HTTP 写动作(POST/PUT/DELETE/PATCH)默认要审批"
    verdict = Verdict.REQUIRE_APPROVAL
    daily_quota = 10

    def matches(self, tool_name: str, args: dict[str, Any]) -> str | None:
        # 适配两种工具:
        # 1) 显式 method 字段(如 http_post tool)
        # 2) tool_name 本身就是 http_post / http_put / etc
        method = args.get("method")
        if isinstance(method, str) and method.upper() in _WRITE_METHODS:
            return f"method={method.upper()}"
        tool_lower = tool_name.lower()
        for verb in _WRITE_METHODS:
            if tool_lower.endswith(f"_{verb.lower()}") or tool_lower == f"http_{verb.lower()}":
                return f"tool={tool_name}"
        return None

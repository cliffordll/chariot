"""agent 写自己源码(`chariot/` 子树)—— self-modification 红线。

B5 wave 1 默认 DENY;wave 3 引入 capabilities.enable_self_mod 后,GuardrailEngine
对这条规则的处理会变成"装 enable_self_mod=True 时 → 升级为 REQUIRE_APPROVAL"。

这条 wave 1 先用最严的语义先 ship,留 wave 3 接 capability 把它升级到
REQUIRE_APPROVAL(走配额 + audit + checkpoint 链路)。

注:本规则不读 chariot.context.references 之类层的实际 cwd —— write_file tool 的
path 通常是 cwd-relative。pattern 检 ``chariot/`` 前缀即可(若 path 显示绝对路径,
也会包含 ``chariot\\`` 子串)。
"""

from __future__ import annotations

import re
from typing import Any

from chariot.guardrails.base import BaseRule, Verdict

_PATTERN = re.compile(r"(?:^|[/\\])chariot[/\\]", re.IGNORECASE)


class SelfModifyChariotRule(BaseRule):
    rule_id = "self_modify_chariot"
    description = "write_file 写 `chariot/` 子树(agent 自我修改;需 enable_self_mod 才放行)"
    verdict = Verdict.DENY
    daily_quota = None
    # B5 wave 3:enable_self_mod=True 时,引擎把 DENY 降级为 REQUIRE_APPROVAL
    capability_gate = "enable_self_mod"

    def matches(self, tool_name: str, args: dict[str, Any]) -> str | None:
        if tool_name != "write_file":
            return None
        path = args.get("path")
        if not isinstance(path, str) or not path:
            return None
        # 末尾加 / 让 pattern 匹配 'chariot' 目录形式
        canonical = path.replace("\\", "/")
        if canonical.startswith("chariot/") or "/chariot/" in canonical:
            m = _PATTERN.search("/" + canonical)
            return m.group(0).strip("/\\") if m else "chariot/"
        return None

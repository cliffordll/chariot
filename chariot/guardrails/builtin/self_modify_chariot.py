"""agent 写自己源码 / 改自己能力定义 —— self-modification 红线。

匹配场景两类:
1. `write_file` 写 `chariot/` 子树(直接改源码)
2. `propose_skill` 提议新 skill(往 skills 表加行,是源码改动的弱化版)

B5 wave 1 默认 DENY;wave 3 引入 capabilities.enable_self_mod 后,GuardrailEngine
对这条规则的处理会变成"装 enable_self_mod=True 时 → 升级为 REQUIRE_APPROVAL",
再由 ApprovalPolicy(`yolo` / 人工 approval)决定放/拒。

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
    description = "改 chariot 自身(write_file `chariot/` 子树 或 propose_skill;需 enable_self_mod 才放行)"
    verdict = Verdict.DENY
    daily_quota = None
    # B5 wave 3:enable_self_mod=True 时,引擎把 DENY 降级为 REQUIRE_APPROVAL
    capability_gate = "enable_self_mod"

    def matches(self, tool_name: str, args: dict[str, Any]) -> str | None:
        # B6 wave 3:propose_skill 整个工具命中,不看 args(往 skills 表加行即视为自改)
        if tool_name == "propose_skill":
            name = args.get("name") if isinstance(args, dict) else None
            return f"propose_skill:{name}" if isinstance(name, str) and name else "propose_skill"
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

"""`rm -rf` 类破坏性删除(B5 wave 1,DENY)。"""

from __future__ import annotations

import re

from chariot.guardrails.base import Verdict
from chariot.guardrails.builtin._shell_base import ShellCommandRule


class ShellRmRfRule(ShellCommandRule):
    rule_id = "shell_rm_rf"
    description = "shell_exec 调用含 `rm -rf` / `rm -fr` / `rmdir /S` 等递归删除"
    verdict = Verdict.DENY
    daily_quota = None  # DENY 类不带配额
    _PATTERN = re.compile(
        r"\b(?:rm\s+(?:-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)|rmdir\s+/[sS])\b",
    )

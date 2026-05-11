"""`chmod 777` / `chmod -R 0777` 类全开权限(B5 wave 1,REQUIRE_APPROVAL)。"""

from __future__ import annotations

import re

from chariot.guardrails.base import Verdict
from chariot.guardrails.builtin._shell_base import ShellCommandRule


class ShellChmodUnsafeRule(ShellCommandRule):
    rule_id = "shell_chmod_unsafe"
    description = "shell_exec 调用 chmod 把权限设成 777 / a+rwx 类全开"
    verdict = Verdict.REQUIRE_APPROVAL
    daily_quota = 5
    _PATTERN = re.compile(
        r"\bchmod\s+(?:-[a-zA-Z]+\s+)*(?:0?777|a\+rwx|ugo\+rwx)\b",
    )

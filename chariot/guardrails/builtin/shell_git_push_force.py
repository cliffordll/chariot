"""`git push --force` / `git push -f` 强推(B5 wave 1,REQUIRE_APPROVAL)。"""

from __future__ import annotations

import re

from chariot.guardrails.base import Verdict
from chariot.guardrails.builtin._shell_base import ShellCommandRule


class ShellGitPushForceRule(ShellCommandRule):
    rule_id = "shell_git_push_force"
    description = "shell_exec 调用 git push -f / --force(--force-with-lease 不算)"
    verdict = Verdict.REQUIRE_APPROVAL
    daily_quota = 3
    _PATTERN = re.compile(
        r"\bgit\s+push\s+(?:[^\s]+\s+)*(?:--force(?!-with-lease)|-f)(?:\b|$)",
    )

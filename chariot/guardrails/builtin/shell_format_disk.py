"""`mkfs.*` / `format ` 格盘命令(B5 wave 1,DENY)。"""

from __future__ import annotations

import re

from chariot.guardrails.base import Verdict
from chariot.guardrails.builtin._shell_base import ShellCommandRule


class ShellFormatDiskRule(ShellCommandRule):
    rule_id = "shell_format_disk"
    description = "shell_exec 调用 mkfs / format 等格盘命令"
    verdict = Verdict.DENY
    daily_quota = None
    _PATTERN = re.compile(
        # 三种形态:`mkfs.<fs>` / `format <DriveLetter>:` / `format /<flag>`
        # `\b` 只在两端真有 word 边界的形态后用,`format X:` 后是 `:`(非 word char),
        # 直接终止即可
        r"\b(?:mkfs\.[a-z0-9]+\b|format\s+[a-zA-Z]:|format\s+/[a-zA-Z]\b)",
        re.IGNORECASE,
    )

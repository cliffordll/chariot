"""`curl ... | sh` / `wget ... | sh` 远程执行(B5 wave 1,DENY)。"""

from __future__ import annotations

import re

from chariot.guardrails.base import Verdict
from chariot.guardrails.builtin._shell_base import ShellCommandRule


class ShellCurlPipeShRule(ShellCommandRule):
    rule_id = "shell_curl_pipe_sh"
    description = "shell_exec 调用把 curl/wget 拉到的内容直接管道给 shell(供应链风险)"
    verdict = Verdict.DENY
    daily_quota = None
    _PATTERN = re.compile(
        r"\b(?:curl|wget)\s+[^|&;]+\|\s*(?:bash|sh|zsh|powershell|pwsh|cmd)\b",
    )

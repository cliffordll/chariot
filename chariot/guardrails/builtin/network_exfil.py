"""可疑的网络外传命令(scp / rsync 到远端 / nc -e)。

B5 wave 1,REQUIRE_APPROVAL —— 真在做 SCP / 数据导出 也常见,所以走配额审批
而不是 DENY。
"""

from __future__ import annotations

import re

from chariot.guardrails.base import Verdict
from chariot.guardrails.builtin._shell_base import ShellCommandRule


class NetworkExfilRule(ShellCommandRule):
    rule_id = "network_exfil"
    description = "shell_exec 调用 scp/rsync 到远端 / `nc -e` 反弹 shell"
    verdict = Verdict.REQUIRE_APPROVAL
    daily_quota = 5
    _PATTERN = re.compile(
        r"\b(?:"
        # scp/rsync 到远端(后续 token 含 user@host: 或 ssh://);非贪婪扫到远端标识
        r"(?:scp|rsync)\s+[^|;&\n]+?(?:@[\w.-]+:|ssh://|rsync://)"
        # nc -e 反弹 shell
        r"|nc\s+(?:-[a-zA-Z]*e[a-zA-Z]*\s+)"
        # curl/wget 上传(-T / --upload-file / -F file=@)
        r"|(?:curl|wget)\s+(?:[^|;\n]+\s+)?(?:-T|--upload-file|-F\s+\S*=@)"
        r")",
    )

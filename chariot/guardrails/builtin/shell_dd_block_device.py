"""`dd of=/dev/...` 直写块设备(B5 wave 1,DENY)。"""

from __future__ import annotations

import re

from chariot.guardrails.base import Verdict
from chariot.guardrails.builtin._shell_base import ShellCommandRule


class ShellDdBlockDeviceRule(ShellCommandRule):
    rule_id = "shell_dd_block_device"
    description = "shell_exec 调用 dd 直写 /dev/ 块设备(可瞬间擦盘)"
    verdict = Verdict.DENY
    daily_quota = None
    _PATTERN = re.compile(r"\bdd\s+(?:[^=\s]+=\S+\s+)*of=/dev/\S+", re.IGNORECASE)

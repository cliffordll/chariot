"""共享 helper:shell_exec 工具规则的基类(提取 command 文本)。

13 条规则里有 6 条针对 `shell_exec`,共享同一个 args["command"] 抽取逻辑。
单独抽出来避免在每个规则里重复 boilerplate。
"""

from __future__ import annotations

import re
from typing import Any

from chariot.guardrails.base import BaseRule


class ShellCommandRule(BaseRule):
    """基类:只检 `shell_exec` 工具 + 抽 args['command'] 文本跑 regex。

    子类设 `_PATTERN: re.Pattern[str]`(命中即触发该规则的 verdict)。
    """

    _PATTERN: re.Pattern[str]

    def matches(self, tool_name: str, args: dict[str, Any]) -> str | None:
        if tool_name != "shell_exec":
            return None
        command = args.get("command")
        if not isinstance(command, str):
            return None
        m = self._PATTERN.search(command)
        if m is None:
            return None
        return m.group(0)

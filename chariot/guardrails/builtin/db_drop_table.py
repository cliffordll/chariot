"""DROP TABLE / DROP DATABASE 类(B5 wave 1,DENY)。

走 shell_exec(sqlite3 / psql / mysql)或将来直接 SQL 工具都能命中。
"""

from __future__ import annotations

import re
from typing import Any

from chariot.guardrails.base import BaseRule, Verdict

_PATTERN = re.compile(r"\bDROP\s+(?:TABLE|DATABASE|SCHEMA)\b", re.IGNORECASE)


class DbDropTableRule(BaseRule):
    rule_id = "db_drop_table"
    description = "args 里出现 DROP TABLE / DROP DATABASE / DROP SCHEMA SQL"
    verdict = Verdict.DENY
    daily_quota = None

    def matches(self, tool_name: str, args: dict[str, Any]) -> str | None:
        # 检所有 str 类型的 arg(shell_exec.command / sql 工具.sql / etc)
        for value in args.values():
            if not isinstance(value, str):
                continue
            m = _PATTERN.search(value)
            if m is not None:
                return m.group(0)
        return None

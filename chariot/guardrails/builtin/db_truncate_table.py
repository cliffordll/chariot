"""TRUNCATE TABLE / DELETE FROM ... 无 WHERE 子句(B5 wave 1,REQUIRE_APPROVAL)。"""

from __future__ import annotations

import re
from typing import Any

from chariot.guardrails.base import BaseRule, Verdict

_TRUNCATE_PATTERN = re.compile(r"\bTRUNCATE\s+TABLE\b", re.IGNORECASE)
# DELETE FROM <tab>  (没有 WHERE);用 negative lookahead 排除"DELETE FROM x WHERE ..."
_DELETE_NO_WHERE_PATTERN = re.compile(
    r"\bDELETE\s+FROM\s+\S+(?:\s*;|\s*$)",
    re.IGNORECASE | re.MULTILINE,
)


class DbTruncateTableRule(BaseRule):
    rule_id = "db_truncate_table"
    description = "TRUNCATE TABLE 或 DELETE FROM(无 WHERE)"
    verdict = Verdict.REQUIRE_APPROVAL
    daily_quota = 3

    def matches(self, tool_name: str, args: dict[str, Any]) -> str | None:
        for value in args.values():
            if not isinstance(value, str):
                continue
            if (m := _TRUNCATE_PATTERN.search(value)) is not None:
                return m.group(0)
            if (m := _DELETE_NO_WHERE_PATTERN.search(value)) is not None:
                # 二次检查:如果这句还有 WHERE / RETURNING / LIMIT 等限定,放过
                snippet = value[m.start() :]
                if re.search(r"\bWHERE\b", snippet, re.IGNORECASE):
                    continue
                return m.group(0)
        return None

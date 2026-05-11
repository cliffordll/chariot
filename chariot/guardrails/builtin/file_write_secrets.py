"""`write_file` 写敏感配置文件(.env / id_rsa / config 等)。

B5 wave 1,REQUIRE_APPROVAL —— 不全拒,但要审批(常见误伤:legit 改 .env)。
"""

from __future__ import annotations

import re
from typing import Any

from chariot.guardrails.base import BaseRule, Verdict

_PATTERN = re.compile(
    r"(?:^|[/\\])(?:\.env|id_rsa(?:\.pub)?|\.aws/credentials|\.npmrc|\.pypirc|known_hosts|authorized_keys)(?:$|[/\\])",
    re.IGNORECASE,
)


class FileWriteSecretsRule(BaseRule):
    rule_id = "file_write_secrets"
    description = "write_file 写 .env / id_rsa / .aws/credentials 等敏感配置"
    verdict = Verdict.REQUIRE_APPROVAL
    daily_quota = 3

    def matches(self, tool_name: str, args: dict[str, Any]) -> str | None:
        if tool_name != "write_file":
            return None
        path = args.get("path")
        if not isinstance(path, str):
            return None
        # 末尾加 / 让 pattern 的 `[/\\]` 简化
        m = _PATTERN.search(path + "/")
        return m.group(0).strip("/\\") if m else None

"""write_file 路径逃出 cwd 子树(B5 wave 1,DENY)。

跟 B3 wave 3 `@file:` resolver 的 path_traversal 红线对齐:agent 写文件
**只能在工作目录里**。绝对路径 / `..` 越级 → 拒。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chariot.guardrails.base import BaseRule, Verdict


class FileWriteOutsideCwdRule(BaseRule):
    rule_id = "file_write_outside_cwd"
    description = "write_file 写到 cwd 子树之外(绝对路径或 ../ 越级)"
    verdict = Verdict.DENY
    daily_quota = None

    def matches(self, tool_name: str, args: dict[str, Any]) -> str | None:
        if tool_name != "write_file":
            return None
        path = args.get("path")
        if not isinstance(path, str) or not path:
            return None
        try:
            cwd = Path.cwd().resolve()
            target = (cwd / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
        except (OSError, ValueError):
            return path  # 解析失败也算嫌疑,拒了再说
        try:
            target.relative_to(cwd)
        except ValueError:
            return path  # 在 cwd 外
        return None

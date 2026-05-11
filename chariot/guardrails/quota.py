"""日配额计数器(B5 wave 1)。

per-rule 计数:UTC 0 点 reset;in-memory dict(per-process)。多 surface
场景下不共享 —— 简化版,后续(B7 RL 起)可换 DB-backed。

封装策略(CLAUDE.md ⭐):
- 单类 `DailyQuotaTracker`,API 三个方法:`peek_remaining` / `try_consume` / `reset_all`
- `_today_key()` 内部 staticmethod 算 UTC 日期 key,日期切换自动 expire
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar


class DailyQuotaTracker:
    """per-rule 每日计数。

    用法::

        q = DailyQuotaTracker()
        rem = q.peek_remaining("shell_chmod_unsafe", quota=5)  # 5
        q.try_consume("shell_chmod_unsafe", quota=5)          # True;剩 4
        ...
        # 跨过 UTC 0 点 → 计数自动重置
    """

    _UNLIMITED: ClassVar[int] = -1  # 内部 sentinel:quota=None 时不计数

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._day_key = self._today_key()

    def peek_remaining(self, rule_id: str, *, quota: int | None) -> int | None:
        """看 rule 今日剩余配额。`quota=None` 不限 → 返 None。"""
        self._maybe_rollover()
        if quota is None:
            return None
        used = self._counts.get(rule_id, 0)
        return max(quota - used, 0)

    def try_consume(self, rule_id: str, *, quota: int | None) -> bool:
        """消耗一次配额;成功返 True,超限返 False。`quota=None` 永远 True。"""
        self._maybe_rollover()
        if quota is None:
            return True
        used = self._counts.get(rule_id, 0)
        if used >= quota:
            return False
        self._counts[rule_id] = used + 1
        return True

    def reset_all(self) -> None:
        """显式清空(测试用)。"""
        self._counts.clear()
        self._day_key = self._today_key()

    def _maybe_rollover(self) -> None:
        today = self._today_key()
        if today != self._day_key:
            self._counts.clear()
            self._day_key = today

    @staticmethod
    def _today_key() -> str:
        """UTC 日期 key(YYYY-MM-DD)。"""
        return datetime.now(UTC).strftime("%Y-%m-%d")

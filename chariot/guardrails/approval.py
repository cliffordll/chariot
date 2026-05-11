"""ApprovalPolicy —— REQUIRE_APPROVAL 类裁决的放行策略(B5 wave 1 stub)。

wave 1:固定 `auto_approve=False`(没 UI 互动 / `--yolo` flag),所以一旦
GuardrailEngine 出 REQUIRE_APPROVAL,工具直接当 DENY 处理(保守版)。

wave 3:接 `chariot/agent/config.py` 的 `Capabilities`(`yolo` flag),走真
auto_approve 路径。本类预留扩展点。
"""

from __future__ import annotations


class ApprovalPolicy:
    """REQUIRE_APPROVAL 决策的"放不放"策略。

    wave 1:不放(无互动 UI 时保守拒);wave 3 接 `--yolo` / capability 后扩展。
    """

    def __init__(self, *, yolo: bool = False) -> None:
        self._yolo = yolo

    @property
    def yolo(self) -> bool:
        return self._yolo

    def auto_approve(self, tool_name: str) -> bool:
        """`--yolo` 模式 → True(放行 + 写 audit);否则 False。"""
        return self._yolo

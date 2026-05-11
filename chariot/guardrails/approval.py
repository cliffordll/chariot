"""ApprovalPolicy —— REQUIRE_APPROVAL 类裁决的放行策略。

wave 1 stub:`auto_approve` 固定看 `yolo` flag(传 True 全放,False 全拒)。
wave 3 起接 `Capabilities`(从 DB 装载 enable_self_mod + per-process yolo)。

封装:
- 接 `Capabilities | None`;无 capabilities → 退化 yolo=False(保守拒)
- 同时保留 `yolo=` kwarg 路径,给测试 / 简单调用直接传 bool
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chariot.agent.config import Capabilities


class ApprovalPolicy:
    """REQUIRE_APPROVAL 决策的"放不放"策略。

    构造形式::

        # 直接传 yolo(测试 / 临时场景)
        ApprovalPolicy(yolo=True)

        # 传 capabilities(生产路径,bootstrap 装填)
        ApprovalPolicy(capabilities=caps)

    `auto_approve(tool_name)`:`yolo` 模式 → True;否则 False(预留 per-tool
    白名单扩展点,wave 3 暂不开)。
    """

    def __init__(
        self,
        *,
        yolo: bool = False,
        capabilities: Capabilities | None = None,
    ) -> None:
        self._capabilities = capabilities
        # capabilities 优先;否则用 kwarg 兜底
        self._yolo = capabilities.yolo if capabilities is not None else yolo

    @property
    def yolo(self) -> bool:
        return self._yolo

    @property
    def capabilities(self) -> Capabilities | None:
        return self._capabilities

    def auto_approve(self, tool_name: str) -> bool:
        """`--yolo` 模式 → True(放行 + 写 audit);否则 False。"""
        del tool_name  # 暂不接 per-tool 白名单
        return self._yolo

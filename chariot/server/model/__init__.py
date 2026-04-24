"""chariot 模型实现层。

`Model` 是协议接口(`base.py`),任何具体模型(mock / 真 LLM / 本地 llama)
都要实现 `respond(protocol, body, *, stream) -> Response`。

Agent 持有一个 Model 实例完成实际响应生成;Model 层**无状态**,不碰 DB
也不记 log —— 那是 Agent 和外层 service 的活。
"""

from __future__ import annotations

from chariot.server.model.base import Model
from chariot.server.model.mock import MockModel, mock_model

__all__ = ["MockModel", "Model", "mock_model"]

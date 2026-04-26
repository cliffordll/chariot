"""chariot 模型实现层。

`Model` 是协议接口(`base.py`),任何具体模型(mock / 真 LLM / 本地 llama)
都要实现 `respond(body, *, stream) -> Response`(0.2.0 起单协议化,无 protocol 参数)。

Agent 持有一个 Model 实例完成实际响应生成;Model 层**无状态**,不碰 DB
也不记 log —— 那是 Agent 和外层 service 的活。

加新 model:
- 写一个新文件 `chariot/server/model/<name>.py`
- 类上挂 `@ModelRegistry.register("type_name")`
- 在本 __init__.py 里 import 一下,触发模块加载 → 装饰器执行 → 注册生效
"""

from __future__ import annotations

# 顺序无关,但都需要 import 让 @ModelRegistry.register 在模块加载时跑
from chariot.server.model.anthropic import AnthropicModel
from chariot.server.model.base import Model
from chariot.server.model.mock import MockModel, mock_model

__all__ = ["AnthropicModel", "MockModel", "Model", "mock_model"]

"""chariot 模型实现层。

`Model` 是协议接口(`base.py`),任何具体模型(mock / 真 LLM / 本地 llama)
都要实现 `respond(body, *, stream) -> Response`(0.2.0 起单协议化,无 protocol 参数)。

Agent 持有一个 Model 实例完成实际响应生成;Model 层**无状态**,不碰 DB
也不记 log —— 那是 Agent 和外层 service 的活。

注册集中在本文件(显式调用,非 import 副作用):

    ModelRegistry.register("mock", MockModel)
    ModelRegistry.register("anthropic", AnthropicModel)

加新 model:写一个新文件 `chariot/server/model/<name>.py` + 在本文件加一行
`ModelRegistry.register("type_name", NewModel)`。
"""

from __future__ import annotations

from chariot.server.model.anthropic import AnthropicModel
from chariot.server.model.mock import MockModel
from chariot.server.model.registry import ModelRegistry

ModelRegistry.register("mock", MockModel)
ModelRegistry.register("anthropic", AnthropicModel)

"""Model 接口定义 —— 聊天模型抽象基类。

任何具体模型(mock / Anthropic 透传 / 本地 llama / 未来后端)继承 `Model`
并实现两个 abstractmethod:
- `from_config(options)` classmethod —— `ModelRegistry` 用它构造实例
- `respond(body, *, stream)` async —— 核心生成逻辑

ABC + abstractmethod 强制子类实现这两件事(缺则实例化即抛 TypeError);
`ModelRegistry.register` 因此不必在运行期再做 `getattr/callable` 兜底校验,
契约由类型系统承载。

职责边界(严格)
----------------
- 无状态:不持有对话历史,每次 `respond` 独立
- 不记日志:Agent 是唯一日志写入者
- 不碰 DB:Model 只做"输入 → 输出"的纯计算 / 外部调用

0.2.0 起 chariot 单协议化(只接 Anthropic Messages),Model 接口去掉 protocol
参数;每个 Model 实现只需要懂 `/v1/messages` 这一种 schema。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Self

from fastapi.responses import Response


class Model(ABC):
    """聊天模型抽象基类。

    实现 checklist:
    1. `name: str` 属性 —— 模型身份标识(写入 logs.model;UI 展示)
    2. `from_config(options) -> Self` classmethod —— `ModelRegistry` 构造契约;
       options 不合法 raise `ConfigError`
    3. `respond(body, *, stream) -> Response` async —— 核心实现
       - `body`:Anthropic Messages 协议的请求原始字节
       - `stream=True` → 返回 `StreamingResponse(media_type="text/event-stream")`
       - 非流模式 → 返回 `Response(media_type="application/json")`
       - 请求合法性问题用 `ServiceError(status=400, ...)`;上游不可达走 502
    """

    name: str

    @classmethod
    @abstractmethod
    def from_config(cls, options: dict[str, Any]) -> Self:
        """从 options dict 构造 Model 实例。"""

    @abstractmethod
    async def respond(self, body: bytes, *, stream: bool) -> Response:
        """处理一次聊天请求。"""

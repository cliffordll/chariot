"""Model 接口定义。

用 `typing.Protocol` 做结构性约束:任何有 `name: str` 属性和
`async respond(protocol, body, *, stream) -> Response` 方法的对象
都是一个合法 `Model`,无需显式继承。这让将来加 adapter
(`AnthropicModel` / `OpenAIModel` / `LocalLlamaModel`)的门槛极低。

**职责边界(严格)**:
- 无状态:不持有对话历史,不记 last seen,每次 `respond()` 独立
- 不记日志:日志由上层 Agent 写
- 不碰 DB:Model 只做"输入 → 输出"的纯计算 / 外部调用
"""

from __future__ import annotations

from typing import Protocol as _Proto
from typing import runtime_checkable

from fastapi.responses import Response

from chariot.shared.protocols import Protocol


@runtime_checkable
class Model(_Proto):
    """聊天模型抽象接口。

    实现 checklist:
    1. `name: str` —— 模型身份标识(写入 logs.model;UI 展示)
    2. `async respond(protocol, body, *, stream) -> Response` —— 核心生成逻辑
       - `protocol`:客户端 hit 的 API 格式,决定响应要按什么 schema 吐
       - `body`:请求原始字节(JSON 之前 model 自己决定怎么解)
       - `stream=True` 时返回 `StreamingResponse`(media_type=text/event-stream)
       - 非流模式返回 `Response`(media_type=application/json)
    3. 请求合法性问题用 `ServiceError(status=400, ...)`;上游不可达之类走 502
    """

    name: str

    async def respond(
        self,
        protocol: Protocol,
        body: bytes,
        *,
        stream: bool,
    ) -> Response: ...

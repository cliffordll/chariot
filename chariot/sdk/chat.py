"""ChatResult —— 非流式一轮 chat 的结果数据类。

调 `ProxyClient.chat_once(...)` 会得到一个 `ChatResult`。

字段
----
- `text`:合并后的 assistant 文本(忽略 tool_use / thinking 等非文本块)
- `usage`:`{"input_tokens", "output_tokens"}`
- `path`:粗粒度路径标签(`"<fmt> · <server host>"`)
- `latency_ms`:HTTP 往返
- `raw_response`:原始 JSON
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from chariot.sdk._adapters import ProtocolAdapter
from chariot.shared.protocols import Protocol


@dataclass
class ChatResult:
    text: str
    usage: dict[str, int]
    path: str
    latency_ms: int
    raw_response: dict[str, Any]

    @classmethod
    def from_response_data(
        cls,
        data: dict[str, Any],
        *,
        adapter: ProtocolAdapter,
        fmt: Protocol,
        server_base_url: str,
        latency_ms: int,
    ) -> ChatResult:
        """从非流响应 dict 组装 ChatResult。`adapter` 负责 text / usage 抽取,
        path 标签由本方法统一格式化(`<fmt> · <host>`)。"""
        host = urlparse(server_base_url).hostname or server_base_url
        return cls(
            text=adapter.extract_text_once(data),
            usage=adapter.extract_usage_once(data),
            path=f"{fmt.value} · {host}",
            latency_ms=latency_ms,
            raw_response=data,
        )

"""ChatResult —— 非流式一轮 chat 的结果数据类(Anthropic Messages 协议)。

调 `ProxyClient.chat_once(...)` 会得到一个 `ChatResult`。

字段
----
- `text`:合并后的 assistant 文本(忽略 tool_use / thinking 等非文本块)
- `usage`:`{"input_tokens", "output_tokens"}`
- `path`:粗粒度路径标签(`"messages · <server host>"`)
- `latency_ms`:HTTP 往返
- `raw_response`:原始 JSON
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlparse


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
        server_base_url: str,
        latency_ms: int,
    ) -> ChatResult:
        """从 Messages 非流响应 dict 组装 ChatResult。"""
        host = urlparse(server_base_url).hostname or server_base_url
        return cls(
            text=cls._extract_text(data),
            usage=cls._extract_usage(data),
            path=f"messages · {host}",
            latency_ms=latency_ms,
            raw_response=data,
        )

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        """合并 content[].type=='text' 的 text 字段。"""
        blocks = data.get("content", [])
        if not isinstance(blocks, list):
            return ""
        parts: list[str] = []
        for b in cast(list[Any], blocks):
            if not isinstance(b, dict):
                continue
            bd = cast(dict[str, Any], b)
            if bd.get("type") != "text":
                continue
            t = bd.get("text", "")
            if isinstance(t, str):
                parts.append(t)
        return "".join(parts)

    @staticmethod
    def _extract_usage(data: dict[str, Any]) -> dict[str, int]:
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return {"input_tokens": 0, "output_tokens": 0}
        u = cast(dict[str, Any], usage)
        return {
            "input_tokens": int(u.get("input_tokens", 0) or 0),
            "output_tokens": int(u.get("output_tokens", 0) or 0),
        }

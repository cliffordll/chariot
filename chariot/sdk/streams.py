"""SDK 侧 SSE → 文本增量 + usage 累加(Anthropic Messages 协议)。

分层
----
- `SseParser`(0.5.0 起搬到 `chariot/sse.py`):协议无关 SSE 字节流解析器,
  本模块从那里 re-export 维持 0.4.x 的 import 路径不变(`from chariot.sdk
  import SseParser` 仍可用)
- `ChatStream`:**有状态**消费器。把 SseParser 吐的事件按 Anthropic Messages
  规范翻成文本增量,同时累加 input/output tokens。CLI / GUI 流式渲染用

0.2.0 起 chariot 单协议化(只接 Messages),`ChatStream` 直接内联事件处理,
不再走 `ProtocolAdapter` 三分支。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast

import httpx

from chariot.shared.sse import SseParser

__all__ = ["ChatStream", "SseParser"]


@dataclass
class ChatStream:
    """流式文本 + 终端 usage 的组合消费器(Anthropic Messages 协议)。

    用法::

        stream = ChatStream()
        async for tok in stream.text_deltas(resp):
            print(tok, end="", flush=True)
        # 流结束后 stream.input_tokens / stream.output_tokens 可用

    内部直接处理 Messages SSE 事件;协议差异不再 delegate 给 adapter
    (0.2.0 起 chariot 单协议化)。
    """

    input_tokens: int = 0
    output_tokens: int = 0

    async def text_deltas(self, resp: httpx.Response) -> AsyncIterator[str]:
        async for event_name, data in SseParser.iter_frames(resp.aiter_bytes()):
            self._update_usage(event_name, data)
            text = self._extract_text_delta(event_name, data)
            if text:
                yield text

    @staticmethod
    def _extract_text_delta(event_name: str | None, data: dict[str, Any]) -> str:
        """从一条 Messages SSE 事件抽文本增量;非文本事件返空串。"""
        etype = event_name or data.get("type")
        if etype != "content_block_delta":
            return ""
        delta = data.get("delta")
        if not isinstance(delta, dict):
            return ""
        d = cast(dict[str, Any], delta)
        if d.get("type") != "text_delta":
            return ""
        text = d.get("text", "")
        return text if isinstance(text, str) else ""

    def _update_usage(self, event_name: str | None, data: dict[str, Any]) -> None:
        """按 Messages 事件累加 usage(message_start 给 input,message_delta 给 output 累计)。"""
        etype = event_name or data.get("type")
        if etype == "message_start":
            msg = data.get("message")
            if isinstance(msg, dict):
                u = cast(dict[str, Any], msg).get("usage")
                if isinstance(u, dict):
                    ud = cast(dict[str, Any], u)
                    self.input_tokens = int(ud.get("input_tokens", 0) or 0)
                    # message_start 可能已带累计 output_tokens(通常 0 或 1)
                    self.output_tokens = int(ud.get("output_tokens", 0) or 0)
        elif etype == "message_delta":
            u = data.get("usage")
            if isinstance(u, dict):
                ud = cast(dict[str, Any], u)
                # Anthropic message_delta.usage.output_tokens 是累计值
                ot = ud.get("output_tokens")
                if isinstance(ot, int):
                    self.output_tokens = ot

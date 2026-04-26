"""SDK 侧 SSE → 文本增量 + usage 累加(Anthropic Messages 协议)。

分层
----
- `SseParser`:协议**无关**的 SSE 字节流解析器。输入 httpx streaming Response,
  输出 `(event_name, data_dict)` 异步迭代器。只懂 SSE 协议本身。
- `ChatStream`:**有状态**消费器。把 SseParser 吐的事件按 Anthropic Messages
  规范翻成文本增量,同时累加 input/output tokens。CLI / GUI 流式渲染用。

0.2.0 起 chariot 单协议化(只接 Messages),`ChatStream` 直接内联事件处理,
不再走 `ProtocolAdapter` 三分支。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast

import httpx


class SseParser:
    """SSE 字节流 → (event_name, data_dict) 迭代器。

    外部用 `SseParser.iter_frames(resp)`;单元测试可直接调 `SseParser._parse_frame`。

    协议约定:
    - 帧分隔符 `\\n\\n` 或 `\\r\\n\\r\\n`
    - 多个 `data:` 行按 `\\n` 拼接后 JSON 解析
    - `data: [DONE]` sentinel 跳过(OpenAI 流结束标志,Messages 不出现但兼容)
    - 空帧 / 纯注释帧(`:` 开头)跳过
    """

    @staticmethod
    async def iter_frames(
        resp: httpx.Response,
    ) -> AsyncIterator[tuple[str | None, dict[str, Any]]]:
        buffer = b""
        async for chunk in resp.aiter_bytes():
            buffer += chunk
            while True:
                sep_idx = -1
                sep_len = 0
                for sep in (b"\r\n\r\n", b"\n\n"):
                    idx = buffer.find(sep)
                    if idx != -1 and (sep_idx == -1 or idx < sep_idx):
                        sep_idx = idx
                        sep_len = len(sep)
                if sep_idx == -1:
                    break
                frame = buffer[:sep_idx]
                buffer = buffer[sep_idx + sep_len :]
                parsed = SseParser._parse_frame(frame)
                if parsed is not None:
                    yield parsed

        # 尾部容忍无结束空行
        if buffer.strip():
            parsed = SseParser._parse_frame(buffer)
            if parsed is not None:
                yield parsed

    @staticmethod
    def _parse_frame(frame: bytes) -> tuple[str | None, dict[str, Any]] | None:
        event_name: str | None = None
        data_lines: list[str] = []
        for raw_line in frame.split(b"\n"):
            line = raw_line.rstrip(b"\r").decode("utf-8", errors="replace")
            if not line or line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].lstrip())
        if not data_lines:
            return None
        data_str = "\n".join(data_lines)
        if data_str.strip() == "[DONE]":
            return None
        try:
            parsed = json.loads(data_str)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        return event_name, cast(dict[str, Any], parsed)


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
        async for event_name, data in SseParser.iter_frames(resp):
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

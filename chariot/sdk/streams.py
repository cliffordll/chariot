"""SDK 侧 SSE → 文本增量 + usage 累加。

分层
----
- `SseParser`:协议**无关**的 SSE 字节流解析器。输入 httpx streaming Response,
  输出 `(event_name, data_dict)` 异步迭代器。只懂 SSE 协议本身。
- `ChatStream`:**有状态**消费器。持一个 adapter + UsageState,把 SseParser 吐
  的事件按协议翻成文本增量,同时累加 usage 字段。CLI / GUI 流式渲染用。

只关心文本、不关心 usage 的场景也用 `ChatStream`,忽略 `.input_tokens` /
`.output_tokens` 即可 —— 额外维护一个"不累加"版本不值得。

加新协议
--------
只需在 `_adapters.py` 写新 adapter,本模块完全不动。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, cast

import httpx

from chariot.sdk._adapters import ProtocolAdapter, UsageState, adapter_for
from chariot.shared.protocols import Protocol


class SseParser:
    """SSE 字节流 → (event_name, data_dict) 迭代器。

    外部用 `SseParser.iter_frames(resp)`;单元测试可直接调 `SseParser._parse_frame`。

    协议约定:
    - 帧分隔符 `\\n\\n` 或 `\\r\\n\\r\\n`
    - 多个 `data:` 行按 `\\n` 拼接后 JSON 解析
    - `data: [DONE]` sentinel 跳过(OpenAI 流结束标志)
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
    """流式文本 + 终端 usage 的组合消费器。

    用法::

        stream = ChatStream(fmt=Protocol.MESSAGES)
        async for tok in stream.text_deltas(resp):
            print(tok, end="", flush=True)
        # 流结束后 stream.input_tokens / stream.output_tokens 可用

    内部把协议差异 delegate 给 `_adapters.py`:ChatStream 本身只管
    "拉 SSE 帧 → 调 adapter 抽文本 / 更新 usage"的编排。
    """

    fmt: Protocol
    _usage: UsageState = field(default_factory=UsageState)

    @property
    def input_tokens(self) -> int:
        return self._usage.input_tokens

    @property
    def output_tokens(self) -> int:
        return self._usage.output_tokens

    @property
    def _adapter(self) -> ProtocolAdapter:
        return adapter_for(self.fmt)

    async def text_deltas(self, resp: httpx.Response) -> AsyncIterator[str]:
        adapter = self._adapter
        async for event_name, data in SseParser.iter_frames(resp):
            adapter.update_usage(event_name, data, self._usage)
            text = adapter.extract_text_delta(event_name, data)
            if text:
                yield text

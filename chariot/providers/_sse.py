"""SSE 字节流解析器(provider 包内私用,0.6.0+)。

迁自 0.5.0 `chariot/shared/sse.py`(逻辑不动)。下划线前缀表示包内私用 ——
唯一调用方是 `providers/builtin/anthropic.py`(后续 OpenAI / 其它走 SSE 协议
的 Provider 也用)。

`shared/sse.py` 在 0.5.0 给 server / sdk 共享,但 0.6.0 撤了 server / sdk
透传字节路径,SSE 解析只剩 Provider 内部用,所以下沉到 providers/。

外部用 `SseParser.iter_frames(byte_iter)`(参数是 `AsyncIterable[bytes]`,
httpx response 传 `resp.aiter_bytes()`);单元测试可直接调 `_parse_frame`。

协议约定:
- 帧分隔符 `\\n\\n` 或 `\\r\\n\\r\\n`
- 多个 `data:` 行按 `\\n` 拼接后 JSON 解析
- `data: [DONE]` sentinel 跳过(OpenAI 流结束标志,Messages 不出现但兼容)
- 空帧 / 纯注释帧(`:` 开头)跳过

模块级零自由函数(CLAUDE.md ⭐)。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any, cast


class SseParser:
    """SSE 字节流 → (event_name, data_dict) 迭代器。"""

    @staticmethod
    async def iter_frames(
        byte_iter: AsyncIterable[bytes],
    ) -> AsyncIterator[tuple[str | None, dict[str, Any]]]:
        buffer = b""
        async for chunk in byte_iter:
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

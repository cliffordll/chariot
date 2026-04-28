"""MockModel — chariot 内置的本地"模型",不发 HTTP、不依赖外部依赖。

0.2.0 起 chariot 单协议化(只接 Anthropic Messages),Mock 跟着收敛 —— 不再用
ProtocolAdapter 三分支,直接生成 Messages 协议响应。

模块级自由函数:零。所有逻辑收在 `MockModel` 类里。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any, ClassVar, cast

from fastapi.responses import Response, StreamingResponse

from chariot.server.model.base import Model
from chariot.server.service.exceptions import ServiceError

_MOCK_MODEL_NAME = "mock-echo-v1"


class MockModel(Model):
    """内置本地模型。生成 Anthropic Messages 协议的 echo 响应(非流 + SSE)。

    类承担三件事:
    1. body 解析(`_parse_body`)+ 抽 user 文本(`_extract_user_text`)
    2. 统一的 echo 包装(`_echo_reply`)
    3. 拼非流响应 / 吐 SSE 事件流(`_build_once_response` / `_stream_events`)

    `respond()` 是 Model 接口实现;其它方法都是实现细节。
    """

    name: str = _MOCK_MODEL_NAME

    # ---- 调试/测试可调的协议节奏 ----
    _CHUNK_CHARS: ClassVar[int] = 4
    _TOKEN_DELAY_SEC: ClassVar[float] = 0.02

    # ---- ModelRegistry 构造契约 ----

    @classmethod
    def from_config(cls, options: dict[str, Any]) -> MockModel:
        """mock 不消费任何 options;签名兼容 ModelRegistry 即可。"""
        del options  # 显式标注未使用,避免 lint 警告
        return cls()

    # ---- Model 接口 ----

    async def respond(self, body: bytes, *, stream: bool) -> Response:
        body_dict = self._parse_body(body)
        reply = self._echo_reply(self._extract_user_text(body_dict))

        if stream:
            return StreamingResponse(
                self._stream_events(reply),
                status_code=200,
                media_type="text/event-stream",
            )

        content = json.dumps(self._build_once_response(reply), ensure_ascii=False).encode("utf-8")
        return Response(content=content, status_code=200, media_type="application/json")

    # ---- body 解析 ----

    @staticmethod
    def _parse_body(body: bytes) -> dict[str, Any]:
        """JSON 解析 + 顶层 dict 校验;非法抛 ServiceError(400)。"""
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ServiceError(
                status=400, code="invalid_json_body", message=f"非法 JSON: {e}"
            ) from e
        if not isinstance(data, dict):
            raise ServiceError(status=400, code="invalid_json_body", message="顶层必须是对象")
        return cast(dict[str, Any], data)

    @staticmethod
    def _extract_user_text(body: dict[str, Any]) -> str:
        """从 Anthropic Messages body 抽最后一条 user 文本;拿不到返空串。"""
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            return ""
        message_items = cast(list[Any], messages)
        for msg in reversed(message_items):
            if not isinstance(msg, dict):
                continue
            msg_dict = cast(dict[str, Any], msg)
            if msg_dict.get("role") != "user":
                continue
            content = msg_dict.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                content_blocks = cast(list[Any], content)
                for block in reversed(content_blocks):
                    if not isinstance(block, dict):
                        continue
                    block_dict = cast(dict[str, Any], block)
                    text = block_dict.get("text")
                    if isinstance(text, str):
                        return text
        return ""

    @staticmethod
    def _echo_reply(src: str) -> str:
        """统一的 echo 包装;空消息给个提示。"""
        if not src.strip():
            return "[mock] 收到空消息,这里是 MockModel 的 echo 回复。"
        return f"[mock echo] {src}"

    # ---- 响应组装 ----

    def _build_once_response(self, reply: str) -> dict[str, Any]:
        """非流式 Messages 响应顶层。"""
        return {
            "id": f"msg_{uuid.uuid4().hex[:24]}",
            "type": "message",
            "role": "assistant",
            "model": self.name,
            "content": [{"type": "text", "text": reply}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": len(reply)},
        }

    async def _stream_events(self, reply: str) -> AsyncIterator[bytes]:
        """Anthropic Messages SSE 事件序列(message_start → ... → message_stop)。"""
        msg_id = f"msg_{uuid.uuid4().hex[:24]}"
        yield self._sse_event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": msg_id,
                    "type": "message",
                    "role": "assistant",
                    "model": self.name,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            },
        )
        yield self._sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        )
        for piece in self._chunk_text(reply):
            yield self._sse_event(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": piece},
                },
            )
            await asyncio.sleep(self._TOKEN_DELAY_SEC)
        yield self._sse_event("content_block_stop", {"type": "content_block_stop", "index": 0})
        yield self._sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": len(reply)},
            },
        )
        yield self._sse_event("message_stop", {"type": "message_stop"})

    # ---- SSE 工具(类内私有,不再被任何模块级函数调用) ----

    @staticmethod
    def _sse_event(name: str, data: dict[str, Any]) -> bytes:
        """格式化一条 named SSE frame。"""
        payload = json.dumps(data, ensure_ascii=False)
        return f"event: {name}\ndata: {payload}\n\n".encode()

    @classmethod
    def _chunk_text(cls, text: str) -> list[str]:
        """把文本切成 `cls._CHUNK_CHARS` 字符一段,模拟 token 流。"""
        if not text:
            return [""]
        n = cls._CHUNK_CHARS
        return [text[i : i + n] for i in range(0, len(text), n)]

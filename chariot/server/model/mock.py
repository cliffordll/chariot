"""MockModel — chariot 内置的本地"模型",不发 HTTP、不依赖外部依赖。

分层
----
- `ProtocolAdapter`(抽象基类):每个 API 协议一份
    - 抽象方法:`extract_user_text` / `build_once_response` / `stream_events`
    - 基类自带的工具方法:`_sse_event` / `_sse_done` / `_chunk_text`
      —— 三个具体 adapter 用 `self._sse_event(...)` 调,不穿模块级函数
- `MessagesAdapter` / `CompletionsAdapter` / `ResponsesAdapter`:三个具体实现
- `MockModel`:实现 `Model` 接口;持 `{Protocol: ProtocolAdapter}` dispatch;
  body 解析(`_parse_body`)也是 MockModel 的静态方法

模块级自由函数:零。所有逻辑收在类里。

加新协议的套路
-------------
写一个 `FooAdapter(ProtocolAdapter)`,`MockModel.__init__` 里挂进 `self._adapters`。
其它不动。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any, cast

from fastapi.responses import Response, StreamingResponse

from chariot.server.service.exceptions import ServiceError
from chariot.shared.protocols import Protocol

_MOCK_MODEL_NAME = "mock-echo-v1"


# ---------- Protocol Adapter 抽象 + 共享工具 ----------


class ProtocolAdapter(ABC):
    """每个 API 协议一个 adapter,封装 "schema 形状 + SSE 事件序列"。

    `MockModel` 持有三个实例,按 Protocol 枚举 dispatch。Adapter **无状态**,
    每个 Protocol 只实例化一次。

    基类同时负责所有 adapter 共用的 SSE 工具;子类在 `stream_events` 里
    直接 `self._sse_event(...)` / `self._chunk_text(...)` / `self._sse_done()`。
    """

    model_name: str = _MOCK_MODEL_NAME
    # 模拟 token 粒度的 SSE 节奏:每帧之间 sleep 这么久
    token_delay_sec: float = 0.02
    # token 流模拟:把文本按固定字符数切块
    chunk_chars: int = 4

    # ---------- 抽象:每个 adapter 必须实现 ----------

    @abstractmethod
    def extract_user_text(self, body: dict[str, Any]) -> str:
        """从请求 body 抽最后一条 user 文本;拿不到就返空串。"""

    @abstractmethod
    def build_once_response(self, reply: str) -> dict[str, Any]:
        """拼非流 JSON 响应 dict(顶层)。"""

    @abstractmethod
    def stream_events(self, reply: str) -> AsyncIterator[bytes]:
        """按协议规范吐 SSE 事件字节流。"""

    # ---------- 共享工具(子类通过 self 调用) ----------

    @staticmethod
    def _sse_event(name: str | None, data: dict[str, Any]) -> bytes:
        """格式化一条 SSE frame;`name=None` 时只发 `data:` 行。"""
        payload = json.dumps(data, ensure_ascii=False)
        if name is not None:
            return f"event: {name}\ndata: {payload}\n\n".encode()
        return f"data: {payload}\n\n".encode()

    @staticmethod
    def _sse_done() -> bytes:
        """OpenAI 家约定的 stream 结束标记。"""
        return b"data: [DONE]\n\n"

    @classmethod
    def _chunk_text(cls, text: str) -> list[str]:
        """把文本切成 `cls.chunk_chars` 字符一段,模拟 token 流。"""
        if not text:
            return [""]
        n = cls.chunk_chars
        return [text[i : i + n] for i in range(0, len(text), n)]


# ---------- Anthropic Messages /v1/messages ----------


class MessagesAdapter(ProtocolAdapter):
    def extract_user_text(self, body: dict[str, Any]) -> str:
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

    def build_once_response(self, reply: str) -> dict[str, Any]:
        return {
            "id": f"msg_{uuid.uuid4().hex[:24]}",
            "type": "message",
            "role": "assistant",
            "model": self.model_name,
            "content": [{"type": "text", "text": reply}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": len(reply)},
        }

    async def stream_events(self, reply: str) -> AsyncIterator[bytes]:
        msg_id = f"msg_{uuid.uuid4().hex[:24]}"
        yield self._sse_event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": msg_id,
                    "type": "message",
                    "role": "assistant",
                    "model": self.model_name,
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
            await asyncio.sleep(self.token_delay_sec)
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


# ---------- OpenAI Chat Completions /v1/chat/completions ----------


class CompletionsAdapter(ProtocolAdapter):
    def extract_user_text(self, body: dict[str, Any]) -> str:
        # 与 Messages 相同的 messages[-1] 抽取逻辑(两种协议 body 都用 messages 数组)
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

    def build_once_response(self, reply: str) -> dict[str, Any]:
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": self.model_name,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": reply},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": len(reply),
                "total_tokens": len(reply),
            },
        }

    async def stream_events(self, reply: str) -> AsyncIterator[bytes]:
        cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        created = int(time.time())

        def frame(delta: dict[str, Any], finish: str | None = None) -> bytes:
            return self._sse_event(
                None,
                {
                    "id": cid,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": self.model_name,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                },
            )

        yield frame({"role": "assistant", "content": ""})
        for piece in self._chunk_text(reply):
            yield frame({"content": piece})
            await asyncio.sleep(self.token_delay_sec)
        yield frame({}, finish="stop")
        yield self._sse_done()


# ---------- OpenAI Responses /v1/responses ----------


class ResponsesAdapter(ProtocolAdapter):
    def extract_user_text(self, body: dict[str, Any]) -> str:
        # 优先 input(string 或 list),退 instructions
        inp = body.get("input")
        if isinstance(inp, str):
            return inp
        if isinstance(inp, list) and inp:
            input_items = cast(list[Any], inp)
            last = input_items[-1]
            if isinstance(last, dict):
                last_dict = cast(dict[str, Any], last)
                content = last_dict.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    content_parts = cast(list[Any], content)
                    for part in reversed(content_parts):
                        if not isinstance(part, dict):
                            continue
                        part_dict = cast(dict[str, Any], part)
                        text = part_dict.get("text")
                        if isinstance(text, str):
                            return text
        instr = body.get("instructions")
        if isinstance(instr, str):
            return instr
        return ""

    def build_once_response(self, reply: str) -> dict[str, Any]:
        rid = f"resp_{uuid.uuid4().hex[:24]}"
        return {
            "id": rid,
            "object": "response",
            "created_at": int(time.time()),
            "status": "completed",
            "model": self.model_name,
            "output": [
                {
                    "id": f"msg_{uuid.uuid4().hex[:24]}",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": reply, "annotations": []}],
                }
            ],
            "output_text": reply,
            "usage": {
                "input_tokens": 0,
                "output_tokens": len(reply),
                "total_tokens": len(reply),
            },
        }

    async def stream_events(self, reply: str) -> AsyncIterator[bytes]:
        rid = f"resp_{uuid.uuid4().hex[:24]}"
        item_id = f"msg_{uuid.uuid4().hex[:24]}"

        yield self._sse_event(
            "response.created",
            {
                "type": "response.created",
                "response": {
                    "id": rid,
                    "object": "response",
                    "status": "in_progress",
                    "model": self.model_name,
                },
            },
        )
        yield self._sse_event(
            "response.output_item.added",
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {
                    "id": item_id,
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                },
            },
        )
        yield self._sse_event(
            "response.content_part.added",
            {
                "type": "response.content_part.added",
                "item_id": item_id,
                "output_index": 0,
                "content_index": 0,
                "part": {"type": "output_text", "text": "", "annotations": []},
            },
        )
        for piece in self._chunk_text(reply):
            yield self._sse_event(
                "response.output_text.delta",
                {
                    "type": "response.output_text.delta",
                    "item_id": item_id,
                    "output_index": 0,
                    "content_index": 0,
                    "delta": piece,
                },
            )
            await asyncio.sleep(self.token_delay_sec)
        yield self._sse_event(
            "response.output_text.done",
            {
                "type": "response.output_text.done",
                "item_id": item_id,
                "output_index": 0,
                "content_index": 0,
                "text": reply,
            },
        )
        yield self._sse_event(
            "response.completed",
            {
                "type": "response.completed",
                "response": {
                    "id": rid,
                    "object": "response",
                    "status": "completed",
                    "model": self.model_name,
                    "output_text": reply,
                },
            },
        )


# ---------- MockModel(Model 接口实现) ----------


class MockModel:
    """内置本地模型。按 Protocol 枚举 dispatch 到对应 adapter。

    类本身只做三件事:
    1. body 解析(`_parse_body` 静态方法,MockModel 独有)
    2. 统一的 echo 包装(`_echo_reply`):三协议 reply 文本一致
    3. 查 adapter → 调 `build_once_response` / `stream_events` → 组装 Response

    **无业务分支** —— 协议差异全在 adapter 里。加新协议不动本类。
    """

    name: str = _MOCK_MODEL_NAME

    def __init__(self) -> None:
        self._adapters: dict[Protocol, ProtocolAdapter] = {
            Protocol.MESSAGES: MessagesAdapter(),
            Protocol.CHAT_COMPLETIONS: CompletionsAdapter(),
            Protocol.RESPONSES: ResponsesAdapter(),
        }

    async def respond(
        self,
        protocol: Protocol,
        body: bytes,
        *,
        stream: bool,
    ) -> Response:
        adapter = self._adapters[protocol]
        body_dict = self._parse_body(body)
        reply = self._echo_reply(adapter.extract_user_text(body_dict))

        if stream:
            return StreamingResponse(
                adapter.stream_events(reply),
                status_code=200,
                media_type="text/event-stream",
            )

        content = json.dumps(adapter.build_once_response(reply), ensure_ascii=False).encode("utf-8")
        return Response(content=content, status_code=200, media_type="application/json")

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
    def _echo_reply(src: str) -> str:
        """统一的 echo 包装;空消息给个提示。三协议共用。"""
        if not src.strip():
            return "[mock] 收到空消息,这里是 MockModel 的 echo 回复。"
        return f"[mock echo] {src}"


# 模块级单例(默认可用);Agent 初始化时默认注入这个
mock_model = MockModel()

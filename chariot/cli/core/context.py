"""chat 会话上下文:客户端 + 会话配置 + 多轮 messages 历史。

`ChatContext` 同时服务一次性命令(`chat.py::_one_shot`)和 REPL(`repl.py`)。
独立于 typer / REPL / 前端 UI,只管"一轮流式请求 + usage 抽取 + 消息历史"。

典型用法
--------
```
ctx = ChatContext(client=client, fmt=Protocol.MESSAGES, model="claude-haiku-4-5")
ctx.append_user("hi")
result = await ctx.run_turn(on_token=print)
ctx.append_assistant(result.text)
```
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from chariot.sdk.client import ProxyClient
from chariot.sdk.streams import ChatStream
from chariot.shared.protocols import Protocol

DEFAULT_MODELS: dict[Protocol, str] = {
    Protocol.MESSAGES: "claude-haiku-4-5",
    Protocol.CHAT_COMPLETIONS: "gpt-4o-mini",
    Protocol.RESPONSES: "gpt-4o-mini",
}


def _empty_messages() -> list[dict[str, str]]:
    """messages 字段的 default_factory;helper 函数显式标注类型避免 pyright 报 Unknown。"""
    return []


@dataclass
class ChatError(Exception):
    """上游 4xx / 5xx 时 run_turn 抛出的异常,body 为响应正文。"""

    status: int
    body: str

    def short_body(self, limit: int = 200) -> str:
        s = self.body.strip()
        return s if len(s) <= limit else s[:limit] + "…"


@dataclass(frozen=True)
class TurnResult:
    """一轮流式请求的收尾结果(替代原 tuple[str, int, int, int] 返回)。"""

    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


@dataclass
class ChatContext:
    """一次聊天会话的完整上下文:客户端 + 会话配置 + 多轮历史。"""

    client: ProxyClient
    fmt: Protocol
    model: str
    max_tokens: int = 1024
    messages: list[dict[str, str]] = field(default_factory=_empty_messages)

    # ---------- 状态操作 ----------

    def append_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def append_assistant(self, text: str) -> None:
        self.messages.append({"role": "assistant", "content": text})

    def pop_last(self) -> None:
        """撤回最后一条消息;REPL 本轮请求失败时用,避免污染后续上下文。"""
        if self.messages:
            self.messages.pop()

    def reset(self) -> None:
        """清空对话历史,保留会话配置(fmt / model / upstream ...)。"""
        self.messages.clear()

    def set_fmt(self, fmt: Protocol) -> None:
        self.fmt = fmt

    def set_model(self, model: str) -> None:
        self.model = model

    # ---------- 核心:一轮请求 ----------

    async def run_turn(self, on_token: Callable[[str], None]) -> TurnResult:
        """用当前 `self.messages` 发一轮流式请求,`on_token` 实时收每个文本增量。

        server 4xx / 5xx 时抛 `ChatError`(body = 响应正文)。
        """
        body = self._build_body()
        stream = ChatStream(fmt=self.fmt)
        buf: list[str] = []
        t0 = time.monotonic()

        async with self.client.stream_chat(self.fmt, body) as resp:
            if resp.status_code >= 400:
                err_bytes = await resp.aread()
                raise ChatError(
                    status=resp.status_code,
                    body=err_bytes.decode("utf-8", errors="replace"),
                )
            async for tok in stream.text_deltas(resp):
                on_token(tok)
                buf.append(tok)

        return TurnResult(
            text="".join(buf),
            input_tokens=stream.input_tokens,
            output_tokens=stream.output_tokens,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    # ---------- 私有:按 format 组装请求体 ----------

    def _build_body(self) -> dict[str, Any]:
        """按 self.fmt 把对话历史组装成请求体。

        v0.1 只存纯文本(`content: str`),三格式的多轮表达都能直接消化。
        """
        if self.fmt is Protocol.MESSAGES:
            return {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "stream": True,
                "messages": self.messages,
            }

        if self.fmt is Protocol.CHAT_COMPLETIONS:
            # include_usage=true 让最后一个 chunk 带 prompt/completion_tokens
            return {
                "model": self.model,
                "stream": True,
                "stream_options": {"include_usage": True},
                "max_tokens": self.max_tokens,
                "messages": self.messages,
            }

        # Protocol.RESPONSES:字段名是 max_output_tokens;input item 按
        # Responses 规范带 type="message"
        return {
            "model": self.model,
            "stream": True,
            "max_output_tokens": self.max_tokens,
            "input": [
                {"type": "message", "role": m["role"], "content": m["content"]}
                for m in self.messages
            ],
        }

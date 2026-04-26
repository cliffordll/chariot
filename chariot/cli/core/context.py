"""chat 会话上下文:客户端 + 会话配置 + 多轮 messages 历史。

`ChatContext` 同时服务一次性命令(`commands/chat.py::_one_shot`)和 REPL(`repl.py`)。
独立于 typer / REPL / 前端 UI,只管"一轮流式请求 + usage 抽取 + 消息历史"。

0.2.0 起 chariot 单协议化(只接 Anthropic Messages),`ChatContext` 跟着收敛 ——
不再持 fmt 字段,_build_body 只产 messages 协议体。

典型用法
--------
```
ctx = ChatContext(client=client, model="claude-haiku-4-5")
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

DEFAULT_MODEL: str = "claude-haiku-4-5"


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
        """清空对话历史,保留会话配置(model / max_tokens)。"""
        self.messages.clear()

    def set_model(self, model: str) -> None:
        self.model = model

    # ---------- 核心:一轮请求 ----------

    async def run_turn(self, on_token: Callable[[str], None]) -> TurnResult:
        """用当前 `self.messages` 发一轮流式请求,`on_token` 实时收每个文本增量。

        server 4xx / 5xx 时抛 `ChatError`(body = 响应正文)。
        """
        body = self._build_body()
        stream = ChatStream()
        buf: list[str] = []
        t0 = time.monotonic()

        async with self.client.stream_chat(body) as resp:
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

    # ---------- 私有:组装请求体 ----------

    def _build_body(self) -> dict[str, Any]:
        """把对话历史组装成 Anthropic Messages 请求体。"""
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "stream": True,
            "messages": self.messages,
        }

"""`chariot chat "问题"` — 一次性模式执行器。

`ChatOnce` 持有 `ChatContext`,跑一轮请求 + 打印 meta 行。与 `ChatRepl` 对称,
都是"`ChatContext` 上的一种执行模式"。典型 caller 是 CLI 命令 `commands/chat.py`。
"""

from __future__ import annotations

from dataclasses import dataclass

import typer

from chariot.cli.context import ChatContext, ChatError
from chariot.cli.render import Renderer


@dataclass
class ChatOnce:
    """一次性聊天执行器:发一条消息 + 流式打印 + meta 行;失败 `typer.Exit(1)`。"""

    ctx: ChatContext

    async def run(self, text: str) -> None:
        self.ctx.append_user(text)
        try:
            result = await self.ctx.run_turn(Renderer.render_event)
        except ChatError as e:
            Renderer.stream_newline()
            Renderer.error_bubble(f"{e.error_type}: {e.short_message()}")
            raise typer.Exit(code=1) from None

        Renderer.stream_newline()
        Renderer.meta_line(
            provider=result.provider_name_snapshot,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=result.latency_ms,
        )

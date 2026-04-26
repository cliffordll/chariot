"""`chariot chat` — 一次性 + REPL 流式聊天。

所有请求打本地 chariot-server,server 里的 Agent(默认 MockModel)生成响应。
0.2.0 起 chariot 单协议化(只接 Anthropic Messages),CLI 不再有 `--protocol`
选项;OpenAI 客户端请通过外部转换器(LiteLLM 等)接入。

flags:
- `--model <id>`(默认 `claude-haiku-4-5`;纯提示字段,mock 不会真用)
- `--max-tokens N`(messages 协议的 max_tokens)
"""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from chariot.cli.core.context import DEFAULT_MODEL, ChatContext
from chariot.cli.core.render import Renderer
from chariot.sdk.client import ProxyClient


def chat_cmd(
    text: Annotated[
        str | None,
        typer.Argument(help="要发送的消息;省略进入 REPL"),
    ] = None,
    model: Annotated[
        str,
        typer.Option("--model", help=f"模型 id;默认 {DEFAULT_MODEL}"),
    ] = DEFAULT_MODEL,
    max_tokens: Annotated[
        int, typer.Option("--max-tokens", help="messages 协议的 max_tokens")
    ] = 1024,
) -> None:
    asyncio.run(
        _run(
            text=text,
            model=model,
            max_tokens=max_tokens,
        )
    )


async def _run(
    *,
    text: str | None,
    model: str,
    max_tokens: int,
) -> None:
    try:
        async with ProxyClient.discover_session(spawn_if_missing=True) as client:
            ctx = ChatContext(
                client=client,
                model=model,
                max_tokens=max_tokens,
            )
            if text is None or not text.strip():
                # 惰性 import 避开模块加载时的环路风险
                from chariot.cli.core.repl import ChatRepl

                await ChatRepl(ctx=ctx).run()
                return

            from chariot.cli.core.once import ChatOnce

            await ChatOnce(ctx=ctx).run(text)
    except RuntimeError as e:
        Renderer.die(f"server 未就绪: {e}")


def register(app: typer.Typer) -> None:
    app.command("chat", help="流式聊天;无参数进 REPL")(chat_cmd)

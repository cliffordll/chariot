"""`chariot chat` — 一次性 + REPL 流式聊天。

所有请求打本地 chariot-server,server 里的 Agent(默认 MockModel)生成响应。
v0 架构下没有"上游"概念,CLI 也不用关心 api key / base url。

flags:
- `--protocol messages | completions | responses`(默认 messages;按哪个 HTTP endpoint 走)
- `--model <id>`(默认按 protocol 取 `DEFAULT_MODELS`;纯提示字段,mock 不会真用)
- `--max-tokens N`(messages 格式的 max_tokens)
"""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from chariot.cli.core.context import DEFAULT_MODELS, ChatContext
from chariot.cli.core.render import Renderer
from chariot.sdk.client import ProxyClient
from chariot.shared.protocols import Protocol


def chat_cmd(
    text: Annotated[
        str | None,
        typer.Argument(help="要发送的消息;省略进入 REPL"),
    ] = None,
    protocol: Annotated[
        str, typer.Option("--protocol", help="messages | completions | responses")
    ] = "messages",
    model: Annotated[
        str | None,
        typer.Option("--model", help="模型 id;默认按 protocol 取 DEFAULT_MODELS"),
    ] = None,
    max_tokens: Annotated[
        int, typer.Option("--max-tokens", help="messages 格式的 max_tokens")
    ] = 1024,
) -> None:
    try:
        fmt = Protocol(protocol)
    except ValueError:
        Renderer.die(f"--protocol 必须是 messages/completions/responses,收到 {protocol!r}")
        return

    effective_model = model or DEFAULT_MODELS[fmt]

    asyncio.run(
        _run(
            text=text,
            fmt=fmt,
            model=effective_model,
            max_tokens=max_tokens,
        )
    )


async def _run(
    *,
    text: str | None,
    fmt: Protocol,
    model: str,
    max_tokens: int,
) -> None:
    try:
        async with ProxyClient.discover_session(spawn_if_missing=True) as client:
            ctx = ChatContext(
                client=client,
                fmt=fmt,
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

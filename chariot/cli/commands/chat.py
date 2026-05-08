"""`chariot chat` — 一次性 + REPL 流式聊天(0.6.0 库化版)。

撤旧 ProxyClient / SDK 路径;直接构造 AIAgent.from_db 实例 + 进程内调
`agent.run(req)`,不再起独立 server。

flags:
- `--model <id>`(默认 `claude-haiku-4-5`;纯 entry name,mock 不会真用)
- `--max-tokens N`(messages 协议的 max_tokens)
- `--convo <id|new>`(0.4.0 + 0.6.0 rename):走 stateful path;`new` → CLI 生成 ULID 并打印;
  ULID 字面量 → 接续该会话;不传 → stateless(单轮 / 不持久化)
"""

from __future__ import annotations

import asyncio
import re
from typing import Annotated

import typer
from ulid import ULID

from chariot.cli._runtime import installed_runtime
from chariot.cli.context import DEFAULT_MODEL, ChatContext
from chariot.cli.render import Renderer

_ULID_RE = re.compile(r"^[0-9A-Z]{26}$")
"""ULID 26 字符。偏宽:Crockford base32 严格排除 I / L / O / U,但 chariot 整体不收紧。"""


def chat_cmd(
    text: Annotated[
        str | None,
        typer.Argument(help="要发送的消息;省略进入 REPL"),
    ] = None,
    model: Annotated[
        str,
        typer.Option("--model", help=f"模型 entry name;默认 {DEFAULT_MODEL}"),
    ] = DEFAULT_MODEL,
    max_tokens: Annotated[
        int, typer.Option("--max-tokens", help="messages 协议的 max_tokens")
    ] = 1024,
    convo: Annotated[
        str | None,
        typer.Option(
            "--convo",
            metavar="new|ULID",
            help=(
                "走 stateful path。值二选一:'new' → 现场生成新 ULID 并打印;"
                "26 字符 ULID 字面量 → 接续该会话。不传 = stateless 单轮"
            ),
        ),
    ] = None,
) -> None:
    convo_id = _resolve_convo_id(convo)
    asyncio.run(
        _run(
            text=text,
            model=model,
            max_tokens=max_tokens,
            convo_id=convo_id,
        )
    )


def _resolve_convo_id(raw: str | None) -> str | None:
    """- None → None(stateless)
    - 'new' → 生成新 ULID,打印 hint 给用户记住,返该 id
    - 26 字符 ULID 字面量 → 原样返
    - 其它(空串 / 非法 ULID) → 立刻 die,避免后续走到 AIAgent 拿到未知 id 报错
    """
    if raw is None:
        return None
    if raw == "new":
        new_id = str(ULID())
        Renderer.out(f"(new convo: {new_id})")
        return new_id
    if _ULID_RE.match(raw):
        return raw
    Renderer.die(
        f"--convo 取值非法: {raw!r};应为 'new' 或 26 字符 ULID(`chariot convo list` 看现有 id)",
    )
    return None  # pragma: no cover · die 已退出


async def _run(
    *,
    text: str | None,
    model: str,
    max_tokens: int,
    convo_id: str | None,
) -> None:
    async with installed_runtime() as agent:
        ctx = ChatContext(
            agent=agent,
            model=model,
            max_tokens=max_tokens,
            convo_id=convo_id,
        )
        if text is None or not text.strip():
            # 惰性 import 避开模块加载时的环路风险
            from chariot.cli.repl import ChatRepl

            await ChatRepl(ctx=ctx).run()
            return

        from chariot.cli.once import ChatOnce

        await ChatOnce(ctx=ctx).run(text)


def register(app: typer.Typer) -> None:
    app.command("chat", help="流式聊天;无参数进 REPL")(chat_cmd)

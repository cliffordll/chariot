"""`chariot chat` — 一次性 + REPL 流式聊天。

所有请求打本地 chariot-server,server 里的 Agent(默认 MockModel)生成响应。
0.2.0 起 chariot 单协议化(只接 Anthropic Messages),CLI 不再有 `--protocol`
选项;OpenAI 客户端请通过外部转换器(LiteLLM 等)接入。

flags:
- `--model <id>`(默认 `claude-haiku-4-5`;纯提示字段,mock 不会真用)
- `--max-tokens N`(messages 协议的 max_tokens)
- `--conversation <id|new>`(0.4.0):走 stateful path;`new` → CLI 生成 ULID 并打印;
  ULID 字面量 → 接续该会话;不传 → stateless(等价 0.3.x 行为)
"""

from __future__ import annotations

import asyncio
import re
from typing import Annotated

import typer
from ulid import ULID

from chariot.cli.core.context import DEFAULT_MODEL, ChatContext
from chariot.cli.core.render import Renderer
from chariot.sdk.client import ProxyClient

_ULID_RE = re.compile(r"^[0-9A-Z]{26}$")
"""ULID 26 字符;跟 server `controller/dataplane.py` 里的 `_ULID_RE` 完全一致。
偏宽:Crockford base32 严格排除 I / L / O / U,但 server 没收紧,CLI 这里也别比 server 严
(否则会出现"CLI 不让传但 server 接受"的反向不一致)。"""


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
    conversation: Annotated[
        str | None,
        typer.Option(
            "--conversation",
            metavar="new|ULID",
            help=(
                "走 stateful path。值二选一:'new' → 现场生成新 ULID 并打印;"
                "26 字符 ULID 字面量 → 接续该会话。不传 = stateless 单轮"
            ),
        ),
    ] = None,
) -> None:
    conv_id = _resolve_conversation_id(conversation)
    asyncio.run(
        _run(
            text=text,
            model=model,
            max_tokens=max_tokens,
            conversation_id=conv_id,
        )
    )


def _resolve_conversation_id(raw: str | None) -> str | None:
    """- None → None(stateless)
    - 'new' → 生成新 ULID,打印 hint 给用户记住,返该 id
    - 26 字符 ULID 字面量 → 原样返
    - 其它(空串 / 非法 ULID) → 立刻 die,避免后续走到 server 拿 400 traceback
    """
    if raw is None:
        return None
    if raw == "new":
        new_id = str(ULID())
        Renderer.out(f"(new conversation: {new_id})")
        return new_id
    if _ULID_RE.match(raw):
        return raw
    Renderer.die(
        f"--conversation 取值非法: {raw!r};"
        "应为 'new' 或 26 字符 ULID(`chariot conversation list` 看现有 id)",
    )
    return None  # pragma: no cover · die 已退出


async def _run(
    *,
    text: str | None,
    model: str,
    max_tokens: int,
    conversation_id: str | None,
) -> None:
    try:
        async with ProxyClient.discover_session(spawn_if_missing=True) as client:
            ctx = ChatContext(
                client=client,
                model=model,
                max_tokens=max_tokens,
                conversation_id=conversation_id,
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

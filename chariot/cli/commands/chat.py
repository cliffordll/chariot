"""`chariot chat` — 一次性 + REPL 流式聊天(0.6.0 库化版)。

撤旧 ProxyClient / SDK 路径;直接构造 AIAgent.from_db 实例 + 进程内调
`agent.run(req)`,不再起独立 server。

flags:
- `--provider <name>`(可选):本次会话用的 provider entry。不传 = 走 DB 默认
  (`chariot provider use <name>` 设置)。两者都没 → die 提示设默认或加 --provider
- `--model <id>`(S.7.3 起,可选):本次会话覆盖 entry.options.model(LLM 真实 id);
  仅 inline 优先级,无 env 兜底
- `--base-url <url>`(S.7.3 起,可选):本次会话覆盖 entry.options.base_url;
  优先级 CLI flag → DB inline → `ANTHROPIC_BASE_URL` env → 默认
- `--api-key <key>`(S.7.3 起,可选):本次会话覆盖 entry.options.api_key;
  优先级 CLI flag → DB inline → `ANTHROPIC_API_KEY` env(或 entry.options.api_key_env)
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

from chariot.agent.exceptions import ConfigError
from chariot.agent.run import AIAgent
from chariot.cli._runtime import installed_runtime
from chariot.cli.context import ChatContext
from chariot.cli.render import Renderer
from chariot.repos.provider_repo import ProviderRepo

_ULID_RE = re.compile(r"^[0-9A-Z]{26}$")
"""ULID 26 字符。偏宽:Crockford base32 严格排除 I / L / O / U,但 chariot 整体不收紧。"""


def chat_cmd(
    text: Annotated[
        str | None,
        typer.Argument(help="要发送的消息;省略进入 REPL"),
    ] = None,
    provider: Annotated[
        str | None,
        typer.Option(
            "--provider",
            help="本次会话用的 provider entry name;不传走 DB 默认",
        ),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option(
            "--model",
            help="本次覆盖 entry.options.model(LLM 真实 id,如 claude-sonnet-4-6)",
        ),
    ] = None,
    base_url: Annotated[
        str | None,
        typer.Option(
            "--base-url",
            help=(
                "本次覆盖 entry.options.base_url;不传按 inline → ANTHROPIC_BASE_URL env → 默认 解析"
            ),
        ),
    ] = None,
    api_key: Annotated[
        str | None,
        typer.Option(
            "--api-key",
            help="本次覆盖 entry.options.api_key;不传按 inline → api_key_env 指向的 env 解析",
        ),
    ] = None,
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
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
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


def _collect_options_overrides(
    *,
    model: str | None,
    base_url: str | None,
    api_key: str | None,
) -> dict[str, str]:
    """把 CLI flag 收集成 options patch dict;空 flag 不进 dict(避免覆盖成空串)。"""
    out: dict[str, str] = {}
    if model is not None:
        out["model"] = model
    if base_url is not None:
        out["base_url"] = base_url
    if api_key is not None:
        out["api_key"] = api_key
    return out


async def _run(
    *,
    text: str | None,
    provider: str | None,
    model: str | None,
    base_url: str | None,
    api_key: str | None,
    max_tokens: int,
    convo_id: str | None,
) -> None:
    async with installed_runtime() as agent:
        provider_name = await _resolve_provider(agent, provider)
        overrides = _collect_options_overrides(model=model, base_url=base_url, api_key=api_key)
        if overrides:
            try:
                await agent.patch_provider_options(provider_name, options_overrides=overrides)
            except ConfigError as e:
                Renderer.die(f"override 失败: {e}")
                return

        ctx = ChatContext(
            agent=agent,
            provider_name=provider_name,
            max_tokens=max_tokens,
            convo_id=convo_id,
        )
        if text is None or not text.strip():
            from chariot.cli.repl import ChatRepl

            await ChatRepl(ctx=ctx).run()
            return

        from chariot.cli.once import ChatOnce

        await ChatOnce(ctx=ctx).run(text)


async def _resolve_provider(agent: AIAgent, override: str | None) -> str:
    """优先级:CLI flag --provider > DB 默认。两者都没 → die 提示设默认。

    返回 entry name(写到 ChatContext.provider_name)。不在这里校验 entry 在
    DB 里存不存在 —— AIAgent.run 路由阶段会发 unknown_provider error,统一处理。
    """
    if override is not None and override.strip():
        return override.strip()

    async with agent.session_maker() as session:
        default = await ProviderRepo(session).get_default()
    if default is None:
        Renderer.die(
            "没有指定 provider:加 `--provider <name>`,或 `chariot provider use <name>` 设个默认",
        )
        # die 会 SystemExit,这里只是为了类型 narrow
        raise SystemExit(1)  # pragma: no cover
    return default.name


def register(app: typer.Typer) -> None:
    app.command("chat", help="流式聊天;无参数进 REPL")(chat_cmd)

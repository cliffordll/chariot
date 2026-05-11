"""`chariot chat` — 一次性 + REPL 流式聊天(0.6.0 库化版)。

撤旧 ProxyClient / SDK 路径;直接构造 AIAgent.bootstrap 实例 + 进程内调
`agent.run_chat(req)`,不再起独立 server。

flags:
- `--provider <name>`(可选):本次会话用的 provider entry。不传 = 走 DB 默认
  (`chariot provider use <name>` 设置)。两者都没 → die 提示设默认或加 --provider
- `--model <id>`(0.6.5+ 起走 per-call ChatRequest.model 字段,不重建 Provider /
  httpx client):本次会话覆盖 LLM 真实 id;仅 inline 优先级,无 env 兜底
- `--base-url <url>`(可选):本次会话覆盖 entry.options.base_url;
  优先级 CLI flag → DB inline → `ANTHROPIC_BASE_URL` env → 默认。**会触发
  Provider 重建 + 走 ClientSpec 命中 / 新建 httpx client**(同 spec 命中复用)
- `--api-key <key>`(可选):本次会话覆盖 entry.options.api_key;同 base_url
  也走 Provider 重建路径
- `--max-tokens N`(messages 协议的 max_tokens)
- `--conversation <id|new>`(主名) / `--convo <id|new>`(兼容别名):走 stateful path;
  `new` → CLI 生成 ULID 并打印;ULID 字面量 → 接续该会话;不传 → stateless
  (单轮 / 不持久化)

`--model` vs `--base-url` / `--api-key` 路径分裂(0.6.5 起):
- `--model` 只切 wire LLM id,不影响 (base_url, api_key) → 不动 ClientSpec →
  零客户端开销;走 ChatContext.model_override → ChatRequest.model
- `--base-url` / `--api-key` 改连接信息 → 走 provider_overrides → AgentRegistry
  重建 Provider 实例,同 (base_url, api_key) 命中 ClientCache(连接 keepalive 保住)
"""

from __future__ import annotations

import asyncio
import re
from typing import Annotated

import typer
from ulid import ULID

from chariot.agent.exceptions import ConfigError
from chariot.cli._runtime import installed_runtime
from chariot.cli.context import ChatContext
from chariot.cli.render import Renderer
from chariot.database.session import DEFAULT_DB_PATH
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
            help=("本次覆盖 entry.options.base_url;不传按 inline → ANTHROPIC_BASE_URL env → 默认 解析"),
        ),
    ] = None,
    api_key: Annotated[
        str | None,
        typer.Option(
            "--api-key",
            help="本次覆盖 entry.options.api_key;不传按 inline → api_key_env 指向的 env 解析",
        ),
    ] = None,
    max_tokens: Annotated[int, typer.Option("--max-tokens", help="messages 协议的 max_tokens")] = 1024,
    conversation: Annotated[
        str | None,
        typer.Option(
            "--conversation",
            "--convo",
            metavar="new|ULID",
            help=(
                "走 stateful path。值二选一:'new' → 现场生成新 ULID 并打印;"
                "26 字符 ULID 字面量 → 接续该会话。不传 = stateless 单轮"
            ),
        ),
    ] = None,
) -> None:
    conversation_id = _resolve_conversation_id(conversation)
    asyncio.run(
        _run(
            text=text,
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            max_tokens=max_tokens,
            conversation_id=conversation_id,
        )
    )


def _resolve_conversation_id(raw: str | None) -> str | None:
    """- None → None(stateless)
    - 'new' → 生成新 ULID,打印 hint 给用户记住,返该 id
    - 26 字符 ULID 字面量 → 原样返
    - 其它(空串 / 非法 ULID) → 立刻 die,避免后续走到 AIAgent 拿到未知 id 报错
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
        f"--conversation/--convo 取值非法: {raw!r};应为 'new' 或 26 字符 ULID(`chariot conversation list` 看现有 id)",
    )
    return None  # pragma: no cover · die 已退出


def _collect_options_overrides(
    *,
    base_url: str | None,
    api_key: str | None,
) -> dict[str, str]:
    """把 CLI flag(连接类)收集成 options patch dict;空 flag 不进 dict
    (避免覆盖成空串)。

    0.6.5 起 `--model` 不在这里 —— 它走 per-call `ChatRequest.model` 字段,
    不需要重建 Provider / ClientSpec。只有改 `(base_url, api_key)` 这种
    "连接维度" override 才需要重建 Provider 拿新 ClientSpec。
    """
    out: dict[str, str] = {}
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
    conversation_id: str | None,
) -> None:
    # Phase 1:开 DB 查默认 provider,把 CLI flag override merge 起来 keyed 到
    # 实际使用的 provider_name。开 DB 用的是 idempotent init_db,后续
    # installed_runtime 内部再 init_db 会复用已装载的 engine
    provider_name = await _resolve_provider_name(provider)

    # Phase 2:把 --base-url / --api-key 收集成 provider_overrides(连接维度,
    # 进 entry.options 重建 Provider + 命中 / 新建 ClientSpec)。`--model` 不在
    # 这里走,它通过 ctx.model_override → ChatRequest.model 实现 per-call 覆盖,
    # 不重建 Provider。
    options_patch = _collect_options_overrides(base_url=base_url, api_key=api_key)
    overrides = {provider_name: options_patch} if options_patch else None

    # Phase 3:installed_runtime 装载 AIAgent(per-session AgentRegistry)+ 跑命令
    try:
        async with installed_runtime(provider_overrides=overrides) as agent:
            ctx = ChatContext(
                agent=agent,
                provider_name=provider_name,
                max_tokens=max_tokens,
                conversation_id=conversation_id,
                model_override=model,
            )
            if text is None or not text.strip():
                from chariot.cli.repl import ChatRepl

                await ChatRepl(ctx=ctx).run()
                return

            from chariot.cli.once import ChatOnce

            await ChatOnce(ctx=ctx).run(text)
    except ConfigError as e:
        Renderer.die(f"AIAgent 装载失败: {e}")


async def _resolve_provider_name(override: str | None) -> str:
    """优先级:CLI flag --provider > DB 默认。两者都没 → die 提示。

    在 installed_runtime 之前调用 —— 用 idempotent `init_db` 临时开 session 查
    默认 provider name。返回 entry name(给 ChatContext.provider_name + provider_overrides
    keying 用);后续 AIAgent.bootstrap 会复用同一个 engine,不会重复 init。
    """
    if override is not None and override.strip():
        return override.strip()

    # 惰性查 default(只用 DB,不构造 Provider 实例 → 不动 ClientCache)
    from chariot.database.session import init_db

    sm = await init_db(DEFAULT_DB_PATH)
    async with sm() as session:
        default = await ProviderRepo(session).get_default()
    if default is None:
        Renderer.die(
            "没有指定 provider:加 `--provider <name>`,或 `chariot provider use <name>` 设个默认",
        )
        raise SystemExit(1)  # pragma: no cover · die 已退出
    return default.name


def register(app: typer.Typer) -> None:
    app.command("chat", help="流式聊天;无参数进 REPL")(chat_cmd)

"""`chariot chat` — 一次性 + REPL 流式聊天(0.6.0 库化版)。

撤旧 ProxyClient / SDK 路径;直接构造 AIAgent.bootstrap 实例 + 进程内调
`agent.run_chat(req)`,不再起独立 server。

flags:
- `--provider <name>`(可选):本次会话用的 provider entry。不传 = 走 DB 默认
  (`chariot provider use <name>` 设置)。两者都没 → die 提示设默认或加 --provider
- `--conversation <id|new>`(主名) / `--convo <id|new>`(兼容别名):走 stateful path;
  `new` → CLI 生成 ULID 并打印;ULID 字面量 → 接续该会话;不传 → stateless
  (单轮 / 不持久化)
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
from chariot.repos.conversation_repo import ConversationRepo

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
    agent: Annotated[
        str | None,
        typer.Option(
            "--agent",
            help=(
                "agent_profile name;非空时 AIAgent 解析后用其 binding:"
                "provider_profile 覆盖 --provider、prompt_bundle 决定 prompt、tool_profile 做 toolset filter。"
            ),
        ),
    ] = None,
    reflect: Annotated[
        bool,
        typer.Option(
            "--reflect/--no-reflect",
            help="B4:开 reflect-then-retry(需 critic 装载;`chariot critic show` 看)",
        ),
    ] = False,
    reflect_retries: Annotated[
        int,
        typer.Option("--reflect-retries", help="reflection 最多重试次数(--reflect 时生效)"),
    ] = 2,
    skill: Annotated[
        str | None,
        typer.Option(
            "--skill",
            help=(
                "B6 wave 2:激活 skill name(`chariot skill list` 看可用)。"
                '覆盖 agent_profile.default_skill;空串 `--skill ""` 显式清空。'
                "dangling reference / disabled skill 走 fallback 不阻断。"
            ),
        ),
    ] = None,
) -> None:
    conversation_id = _resolve_conversation_id(conversation)
    asyncio.run(
        _run(
            text=text,
            provider=provider,
            conversation_id=conversation_id,
            agent_profile=agent,
            reflection_enabled=reflect,
            reflection_max_retries=reflect_retries,
            skill=skill,
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


async def _run(
    *,
    text: str | None,
    provider: str | None,
    conversation_id: str | None,
    agent_profile: str | None,
    reflection_enabled: bool = False,
    reflection_max_retries: int = 2,
    skill: str | None = None,
) -> None:
    # Phase 0:若 --conversation <id>,先查 DB 恢复 agent 配置
    restored_agent = await _restore_conversation_config(conversation_id)

    # Phase 1:开 DB 查默认 provider
    provider_name = provider or _resolve_provider_name(None)
    resolved_agent = agent_profile or restored_agent

    # Phase 2:installed_runtime 装载 AIAgent(per-session AgentRegistry)+ 跑命令
    try:
        async with installed_runtime() as agent:
            ctx = ChatContext(
                agent=agent,
                provider_name=provider_name,
                conversation_id=conversation_id,
                agent_profile=resolved_agent,
                reflection_enabled=reflection_enabled,
                reflection_max_retries=reflection_max_retries,
                skill=skill,
            )
            if text is None or not text.strip():
                from chariot.cli.repl import ChatRepl

                await ChatRepl(ctx=ctx).run()
                return

            from chariot.cli.once import ChatOnce

            await ChatOnce(ctx=ctx).run(text)
    except ConfigError as e:
        Renderer.die(f"AIAgent 装载失败: {e}")


async def _restore_conversation_config(conversation_id: str | None) -> str | None:
    """若给了 conversation_id,查 DB 恢复该对话的 agent 配置。
    返回 restored_agent;无 conversation_id 或找不到 → None。
    provider 由 agent_profile 绑定自动推导,不再单独存储。
    """
    if conversation_id is None:
        return None
    from chariot.database.session import init_db
    from chariot.services.conversation import ConversationService

    sm = await init_db(DEFAULT_DB_PATH)
    async with sm() as session:
        conv = await ConversationService(ConversationRepo(session)).get(conversation_id)
    if conv is None:
        return None
    return conv.agent_profile


def _resolve_provider_name(override: str | None) -> str | None:
    """CLI flag --provider 显式指定时返回它,否则 None(不自动查 DB 默认)。"""
    if override is not None and override.strip():
        return override.strip()
    return None


def register(app: typer.Typer) -> None:
    app.command("chat", help="流式聊天;无参数进 REPL")(chat_cmd)

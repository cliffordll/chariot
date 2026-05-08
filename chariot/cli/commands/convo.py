"""`chariot convo list / show / rm / rename` —— 多轮会话管理。

0.6.0 库化版:撤旧 ProxyClient,直接走 ConvoRepo。

二级子命令组(typer.Typer 嵌套):

只读:
- `chariot convo list [--limit N] [--offset M]`:列 convos
  按 updated_at desc 排序,展示 id / title / last_model / message_count

写:
- `chariot convo show <id>`:详情 + 最近 N 条 messages 摘要
- `chariot convo rm <id>`:删除(cascade messages)
- `chariot convo rename <id> <new-title>`:改 title
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

import typer

from chariot.agent.exceptions import ConvoNotFound
from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.repos.convo_repo import ConvoRepo

convo_app = typer.Typer(
    name="convo",
    help="管理多轮会话(0.4.0 起;0.6.0 起 conversation → convo)",
    no_args_is_help=True,
)


def _truncate(text: str, n: int = 60) -> str:
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


# ---------- list ----------


@convo_app.command("list", help="列 convos(updated_at 降序)")
def list_cmd(
    limit: Annotated[int, typer.Option("--limit", "-n", help="返回上限")] = 50,
    offset: Annotated[int, typer.Option("--offset", help="跳过前 N 条")] = 0,
) -> None:
    asyncio.run(_list(limit, offset))


async def _list(limit: int, offset: int) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        convos = await ConvoRepo(session).list_entries(limit=limit, offset=offset)

    if not convos:
        Renderer.out("(没有会话 — 用 `chariot chat --convo new` 开始一个)")
        return

    rows = [
        (
            c.id,
            _truncate(c.title or "(无标题)", 30),
            c.last_model or "-",
            str(c.message_count),
        )
        for c in convos
    ]
    Renderer.table(["id", "title", "last_model", "msgs"], rows, title="convos")


# ---------- show ----------


@convo_app.command("show", help="展示一条 convo 的详情 + messages 摘要")
def show_cmd(
    convo_id: Annotated[str, typer.Argument(help="convo id (ULID)")],
    tail: Annotated[int, typer.Option("--tail", help="只展示最后 N 条 messages")] = 20,
) -> None:
    asyncio.run(_show(convo_id, tail))


async def _show(convo_id: str, tail: int) -> None:
    async with installed_runtime() as agent, agent.session_maker() as session:
        repo = ConvoRepo(session)
        convo = await repo.get(convo_id)
        if convo is None:
            Renderer.die(f"未知 convo id: {convo_id!r}")
            return
        msgs = await repo.list_messages(convo_id)

    Renderer.out(f"id:           {convo.id}")
    Renderer.out(f"title:        {convo.title or '(无)'}")
    Renderer.out(f"last_model:   {convo.last_model or '-'}")
    Renderer.out(f"message_count: {convo.message_count}")
    Renderer.out(f"created_at:   {convo.created_at.isoformat()}")
    Renderer.out(f"updated_at:   {convo.updated_at.isoformat()}")
    Renderer.out("")

    tail_msgs = msgs[-tail:]
    if not tail_msgs:
        Renderer.out("(没有 messages)")
        return
    Renderer.out(f"--- last {len(tail_msgs)} messages ---")
    for m in tail_msgs:
        # content 是 JSON;还原后判形态:str 或 anthropic blocks 数组
        try:
            content_data = json.loads(m.content)
        except json.JSONDecodeError:
            content_data = m.content
        if isinstance(content_data, str):
            preview = _truncate(content_data, 80)
        else:
            preview = _truncate(json.dumps(content_data, ensure_ascii=False), 80)
        marker = f" [{m.provider_name}]" if m.provider_name else ""
        Renderer.out(f"#{m.seq:3d} {m.role:9s}{marker}: {preview}")


# ---------- rm ----------


@convo_app.command("rm", help="删除会话(cascade messages)")
def rm_cmd(
    convo_id: Annotated[str, typer.Argument(help="convo id (ULID)")],
) -> None:
    asyncio.run(_rm(convo_id))


async def _rm(convo_id: str) -> None:
    async with installed_runtime() as agent:
        try:
            async with agent.session_maker() as session:
                await ConvoRepo(session).delete(convo_id)
        except ConvoNotFound as e:
            Renderer.die(f"删除失败: {e}")
            return
    Renderer.out(f"- {convo_id}")


# ---------- rename ----------


@convo_app.command("rename", help="改 title")
def rename_cmd(
    convo_id: Annotated[str, typer.Argument(help="convo id (ULID)")],
    title: Annotated[str, typer.Argument(help="新 title;空字符串清空标题")],
) -> None:
    asyncio.run(_rename(convo_id, title))


async def _rename(convo_id: str, title: str) -> None:
    new_title: str | None = title if title else None
    async with installed_runtime() as agent:
        try:
            async with agent.session_maker() as session:
                convo = await ConvoRepo(session).update_title(convo_id, new_title)
        except ConvoNotFound as e:
            Renderer.die(f"重命名失败: {e}")
            return
    Renderer.out(f"~ {convo.id}: {convo.title or '(无标题)'}")


def register(app: typer.Typer) -> None:
    app.add_typer(convo_app)

"""`chariot conversation list / show / rm / rename` —— 多轮会话管理(0.4.0)。

二级子命令组(typer.Typer 嵌套):

只读:
- `chariot conversation list [--limit N] [--offset M]`:列 conversations
  按 updated_at desc 排序,展示 id / title / last_model / message_count

写:
- `chariot conversation show <id>`:详情 + 最近 N 条 messages 摘要
- `chariot conversation rm <id>`:删除(cascade messages)
- `chariot conversation rename <id> <new-title>`:改 title

server 未运行 → 错误退出(不自动 spawn,这些都是对运行中 server 的操作)。
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

import httpx
import typer

from chariot.cli.core.render import Renderer
from chariot.sdk.client import ProxyClient

conversation_app = typer.Typer(
    name="conversation",
    help="管理多轮会话(0.4.0 起)",
    no_args_is_help=True,
)


def _truncate(text: str, n: int = 60) -> str:
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


# ---------- list ----------


@conversation_app.command("list", help="列 conversations(updated_at 降序)")
def list_cmd(
    limit: Annotated[int, typer.Option("--limit", "-n", help="返回上限")] = 50,
    offset: Annotated[int, typer.Option("--offset", help="跳过前 N 条")] = 0,
) -> None:
    asyncio.run(_list(limit, offset))


async def _list(limit: int, offset: int) -> None:
    try:
        async with ProxyClient.discover_session(spawn_if_missing=False) as client:
            resp = await client.list_conversations(limit=limit, offset=offset)
    except RuntimeError:
        Renderer.die("server 未运行;先 `chariot start` 起一份")
        return

    if not resp.items:
        Renderer.out("(没有会话 — 用 `chariot chat --conversation new` 开始一个)")
        return

    rows = [
        (
            it.id,
            _truncate(it.title or "(无标题)", 30),
            it.last_model or "-",
            str(it.message_count),
        )
        for it in resp.items
    ]
    Renderer.table(["id", "title", "last_model", "msgs"], rows, title="conversations")


# ---------- show ----------


@conversation_app.command("show", help="展示一条 conversation 的详情 + messages 摘要")
def show_cmd(
    conv_id: Annotated[str, typer.Argument(help="conversation id (ULID)")],
    tail: Annotated[int, typer.Option("--tail", help="只展示最后 N 条 messages")] = 20,
) -> None:
    asyncio.run(_show(conv_id, tail))


async def _show(conv_id: str, tail: int) -> None:
    try:
        async with ProxyClient.discover_session(spawn_if_missing=False) as client:
            try:
                detail = await client.get_conversation(conv_id)
            except httpx.HTTPStatusError as e:
                Renderer.die(f"获取失败 (HTTP {e.response.status_code}): {e.response.text}")
                return
    except RuntimeError:
        Renderer.die("server 未运行;先 `chariot start` 起一份")
        return

    c = detail.conversation
    Renderer.out(f"id:           {c.id}")
    Renderer.out(f"title:        {c.title or '(无)'}")
    Renderer.out(f"last_model:   {c.last_model or '-'}")
    Renderer.out(f"message_count: {c.message_count}")
    Renderer.out(f"created_at:   {c.created_at.isoformat()}")
    Renderer.out(f"updated_at:   {c.updated_at.isoformat()}")
    Renderer.out("")

    msgs = detail.messages[-tail:]
    if not msgs:
        Renderer.out("(没有 messages)")
        return
    Renderer.out(f"--- last {len(msgs)} messages ---")
    for m in msgs:
        # content 可能是 str 或 anthropic blocks 数组;前者直接展,后者 JSON 简短化
        if isinstance(m.content, str):
            preview = _truncate(m.content, 80)
        else:
            preview = _truncate(json.dumps(m.content, ensure_ascii=False), 80)
        marker = f" [{m.model_name}]" if m.model_name else ""
        Renderer.out(f"#{m.seq:3d} {m.role:9s}{marker}: {preview}")


# ---------- rm ----------


@conversation_app.command("rm", help="删除会话(cascade messages)")
def rm_cmd(
    conv_id: Annotated[str, typer.Argument(help="conversation id (ULID)")],
) -> None:
    asyncio.run(_rm(conv_id))


async def _rm(conv_id: str) -> None:
    try:
        async with ProxyClient.discover_session(spawn_if_missing=False) as client:
            try:
                await client.delete_conversation(conv_id)
            except httpx.HTTPStatusError as e:
                Renderer.die(f"删除失败 (HTTP {e.response.status_code}): {e.response.text}")
                return
    except RuntimeError:
        Renderer.die("server 未运行;先 `chariot start` 起一份")
        return
    Renderer.out(f"- {conv_id}")


# ---------- rename ----------


@conversation_app.command("rename", help="改 title")
def rename_cmd(
    conv_id: Annotated[str, typer.Argument(help="conversation id (ULID)")],
    title: Annotated[str, typer.Argument(help="新 title;空字符串清空标题")],
) -> None:
    asyncio.run(_rename(conv_id, title))


async def _rename(conv_id: str, title: str) -> None:
    new_title: str | None = title if title else None
    try:
        async with ProxyClient.discover_session(spawn_if_missing=False) as client:
            try:
                conv = await client.update_conversation(conv_id, title=new_title)
            except httpx.HTTPStatusError as e:
                Renderer.die(f"重命名失败 (HTTP {e.response.status_code}): {e.response.text}")
                return
    except RuntimeError:
        Renderer.die("server 未运行;先 `chariot start` 起一份")
        return
    Renderer.out(f"~ {conv.id}: {conv.title or '(无标题)'}")


def register(app: typer.Typer) -> None:
    app.add_typer(conversation_app)

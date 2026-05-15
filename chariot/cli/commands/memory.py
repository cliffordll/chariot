"""`chariot memory` commands."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

import typer

from chariot.agent.config import ConfigError
from chariot.cli._runtime import installed_runtime
from chariot.cli.render import Renderer
from chariot.services.memory import MemoryService

memory_app = typer.Typer(
    name="memory",
    help="管理长期记忆 entries / events / links",
    no_args_is_help=True,
)


def _fmt_dt(value: Any) -> str:
    if value is None:
        return "-"
    if hasattr(value, "isoformat"):
        return value.isoformat(timespec="seconds")
    return str(value)


def _parse_meta(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        Renderer.die(f"meta must be valid JSON: {exc}")
        raise SystemExit(1) from exc
    if not isinstance(data, dict):
        Renderer.die("meta must be a JSON object")
        raise SystemExit(1) from None
    return data


def _build_links(
    conversations: list[str],
    providers: list[str],
    tags: list[str],
) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    for conversation in conversations:
        if conversation.strip():
            links.append({"link_type": "conversation", "link_value": conversation.strip()})
    for provider in providers:
        if provider.strip():
            links.append({"link_type": "provider", "link_value": provider.strip()})
    for tag in tags:
        if tag.strip():
            links.append({"link_type": "tag", "link_value": tag.strip()})
    return links


@memory_app.command("list", help="列出 memory entries")
def list_cmd() -> None:
    asyncio.run(_list())


@memory_app.command("show", help="查看单条 memory")
def show_cmd(
    memory_id: Annotated[str, typer.Argument(help="memory id")],
) -> None:
    asyncio.run(_show(memory_id))


@memory_app.command("add", help="创建 memory")
def add_cmd(
    kind: Annotated[str, typer.Option("--kind", help="memory 类型")] = "preference",
    text: Annotated[str, typer.Option("--text", help="memory 文本")] = "",
    meta: Annotated[str, typer.Option("--meta", help="JSON meta")] = "",
    conversation: Annotated[list[str], typer.Option("--conversation", help="关联 conversation id")] = [],
    provider: Annotated[list[str], typer.Option("--provider", help="关联 provider name")] = [],
    tag: Annotated[list[str], typer.Option("--tag", help="关联 tag")] = [],
    pinned: Annotated[bool, typer.Option("--pinned", help="是否置顶")] = False,
    archived: Annotated[bool, typer.Option("--archived", help="是否归档")] = False,
) -> None:
    asyncio.run(
        _add(
            kind=kind,
            text=text,
            meta=meta,
            conversation=conversation,
            provider=provider,
            tag=tag,
            pinned=pinned,
            archived=archived,
        )
    )


@memory_app.command("update", help="更新 memory")
def update_cmd(
    memory_id: Annotated[str, typer.Argument(help="memory id")],
    kind: Annotated[str, typer.Option("--kind", help="memory 类型")] = "",
    text: Annotated[str, typer.Option("--text", help="memory 文本")] = "",
    meta: Annotated[str, typer.Option("--meta", help="JSON meta")] = "",
    conversation: Annotated[list[str], typer.Option("--conversation", help="关联 conversation id")] = [],
    provider: Annotated[list[str], typer.Option("--provider", help="关联 provider name")] = [],
    tag: Annotated[list[str], typer.Option("--tag", help="关联 tag")] = [],
    pinned: Annotated[str, typer.Option("--pinned", help="yes/no/empty")] = "",
    archived: Annotated[str, typer.Option("--archived", help="yes/no/empty")] = "",
) -> None:
    asyncio.run(
        _update(
            memory_id,
            kind=kind,
            text=text,
            meta=meta,
            conversation=conversation,
            provider=provider,
            tag=tag,
            pinned=pinned,
            archived=archived,
        )
    )


@memory_app.command("delete", help="删除 memory")
def delete_cmd(
    memory_id: Annotated[str, typer.Argument(help="memory id")],
) -> None:
    asyncio.run(_delete(memory_id))


@memory_app.command("pin", help="置顶 memory")
def pin_cmd(
    memory_id: Annotated[str, typer.Argument(help="memory id")],
) -> None:
    asyncio.run(_pin(memory_id, True))


@memory_app.command("unpin", help="取消置顶 memory")
def unpin_cmd(
    memory_id: Annotated[str, typer.Argument(help="memory id")],
) -> None:
    asyncio.run(_pin(memory_id, False))


@memory_app.command("archive", help="归档 memory")
def archive_cmd(
    memory_id: Annotated[str, typer.Argument(help="memory id")],
) -> None:
    asyncio.run(_archive(memory_id, True))


@memory_app.command("restore", help="恢复已归档 memory")
def restore_cmd(
    memory_id: Annotated[str, typer.Argument(help="memory id")],
) -> None:
    asyncio.run(_archive(memory_id, False))


@memory_app.command("events", help="列出 memory events")
def events_cmd(
    memory_id: Annotated[str, typer.Option("--memory-id", help="仅看某条 memory")] = "",
) -> None:
    asyncio.run(_events(memory_id or None))


@memory_app.command("links", help="列出 memory links")
def links_cmd(
    memory_id: Annotated[str, typer.Option("--memory-id", help="仅看某条 memory")] = "",
) -> None:
    asyncio.run(_links(memory_id or None))


@memory_app.command("search", help="搜索 memory")
def search_cmd(
    query: Annotated[str, typer.Argument(help="搜索关键字")],
) -> None:
    asyncio.run(_search(query))


async def _list() -> None:
    async with installed_runtime() as agent:
        entries = await MemoryService(agent).list_entries()
    if not entries:
        Renderer.out("(没有 memory entries)")
        return
    rows = [
        (
            entry.id,
            entry.kind,
            "yes" if entry.pinned else "no",
            "yes" if entry.archived else "no",
            _truncate(entry.text, 72),
        )
        for entry in entries
    ]
    Renderer.table(["id", "kind", "pinned", "archived", "text"], rows, title="memory")


async def _show(memory_id: str) -> None:
    async with installed_runtime() as agent:
        service = MemoryService(agent)
        entry = await service.get_entry(memory_id)
        if entry is None:
            Renderer.die(f"memory not found: {memory_id!r}")
            return
        links = await service.list_links(memory_id=memory_id)
        events = await service.list_events(memory_id=memory_id)
    Renderer.kv(
        {
            "id": entry.id,
            "kind": entry.kind,
            "pinned": "yes" if entry.pinned else "no",
            "archived": "yes" if entry.archived else "no",
            "created_at": _fmt_dt(entry.created_at),
            "updated_at": _fmt_dt(entry.updated_at),
        }
    )
    Renderer.out("")
    Renderer.out("text:")
    Renderer.out(entry.text)
    Renderer.out("")
    Renderer.out("meta:")
    Renderer.out(json.dumps(entry.meta, ensure_ascii=False, indent=2, sort_keys=True))
    if links:
        Renderer.out("")
        Renderer.out("links:")
        Renderer.table(
            ["type", "value", "created_at"],
            [(link.link_type, link.link_value, _fmt_dt(link.created_at)) for link in links],
            title="memory links",
        )
    if events:
        Renderer.out("")
        Renderer.out("events:")
        Renderer.table(
            ["event", "created_at"],
            [(event.event_type, _fmt_dt(event.created_at)) for event in events],
            title="memory events",
        )


async def _add(
    *,
    kind: str,
    text: str,
    meta: str,
    conversation: list[str],
    provider: list[str],
    tag: list[str],
    pinned: bool,
    archived: bool,
) -> None:
    if not text.strip():
        Renderer.die("--text is required")
        return
    async with installed_runtime() as agent:
        try:
            entry = await MemoryService(agent).create(
                kind=kind.strip() or "preference",
                text=text,
                meta=_parse_meta(meta),
                pinned=pinned,
                archived=archived,
                links=_build_links(conversation, provider, tag),
            )
        except ConfigError as exc:
            Renderer.die(f"create failed: {exc}")
            return
        await agent.audit_hooks.record_memory_store(
            memory_id=entry.id,
            action="create",
            kind=entry.kind,
            pinned=entry.pinned,
        )
    Renderer.out(f"+ {entry.id} {entry.kind}")


async def _update(
    memory_id: str,
    *,
    kind: str,
    text: str,
    meta: str,
    conversation: list[str],
    provider: list[str],
    tag: list[str],
    pinned: str,
    archived: str,
) -> None:
    changes: dict[str, Any] = {}
    if kind.strip():
        changes["kind"] = kind.strip()
    if text.strip():
        changes["text"] = text
    parsed_meta = _parse_meta(meta)
    if parsed_meta is not None:
        changes["meta"] = parsed_meta
    if pinned.strip():
        changes["pinned"] = _parse_bool_word(pinned)
    if archived.strip():
        changes["archived"] = _parse_bool_word(archived)
    links = _build_links(conversation, provider, tag)
    if links:
        changes["links"] = links
    if not changes:
        Renderer.die("at least one field is required")
        return
    async with installed_runtime() as agent:
        try:
            entry = await MemoryService(agent).update(memory_id, **changes)
        except ConfigError as exc:
            Renderer.die(f"update failed: {exc}")
            return
        await agent.audit_hooks.record_memory_store(
            memory_id=entry.id,
            action="update",
            kind=entry.kind,
            pinned=entry.pinned,
        )
    Renderer.out(f"~ {entry.id} {entry.kind}")


async def _delete(memory_id: str) -> None:
    async with installed_runtime() as agent:
        try:
            await MemoryService(agent).delete(memory_id)
        except ConfigError as exc:
            Renderer.die(f"delete failed: {exc}")
            return
        await agent.audit_hooks.record_memory_store(
            memory_id=memory_id,
            action="delete",
        )
    Renderer.out(f"- {memory_id}")


async def _pin(memory_id: str, pinned: bool) -> None:
    async with installed_runtime() as agent:
        try:
            entry = await MemoryService(agent).pin(memory_id, pinned=pinned)
        except ConfigError as exc:
            Renderer.die(f"pin failed: {exc}")
            return
        await agent.audit_hooks.record_memory_store(
            memory_id=entry.id,
            action="pin" if pinned else "unpin",
            kind=entry.kind,
            pinned=entry.pinned,
        )
    Renderer.out(f"* {entry.id}: {'pinned' if entry.pinned else 'unpinned'}")


async def _archive(memory_id: str, archived: bool) -> None:
    async with installed_runtime() as agent:
        try:
            entry = await MemoryService(agent).archive(memory_id, archived=archived)
        except ConfigError as exc:
            Renderer.die(f"archive failed: {exc}")
            return
        await agent.audit_hooks.record_memory_store(
            memory_id=entry.id,
            action="archive" if archived else "restore",
            kind=entry.kind,
            pinned=entry.pinned,
        )
    Renderer.out(f"* {entry.id}: {'archived' if entry.archived else 'restored'}")


async def _events(memory_id: str | None) -> None:
    async with installed_runtime() as agent:
        entries = await MemoryService(agent).list_events(memory_id=memory_id)
    if not entries:
        Renderer.out("(没有 memory events)")
        return
    rows = [(event.memory_id, event.event_type, _fmt_dt(event.created_at)) for event in entries]
    Renderer.table(["memory", "event", "created_at"], rows, title="memory events")


async def _links(memory_id: str | None) -> None:
    async with installed_runtime() as agent:
        entries = await MemoryService(agent).list_links(memory_id=memory_id)
    if not entries:
        Renderer.out("(没有 memory links)")
        return
    rows = [(link.memory_id, link.link_type, link.link_value, _fmt_dt(link.created_at)) for link in entries]
    Renderer.table(["memory", "type", "value", "created_at"], rows, title="memory links")


async def _search(query: str) -> None:
    async with installed_runtime() as agent:
        entries = await MemoryService(agent).search_entries(query)
    if not entries:
        Renderer.out("(没有匹配的 memory)")
        return
    rows = [(entry.id, entry.kind, "yes" if entry.pinned else "no", _truncate(entry.text, 72)) for entry in entries]
    Renderer.table(["id", "kind", "pinned", "text"], rows, title=f"memory search: {query}")


def _parse_bool_word(raw: str) -> bool:
    word = raw.strip().lower()
    if word in {"1", "true", "yes", "y", "on"}:
        return True
    if word in {"0", "false", "no", "n", "off"}:
        return False
    raise ConfigError(f"invalid boolean value: {raw!r}")


def _truncate(text: str, limit: int = 80) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def register(app: typer.Typer) -> None:
    app.add_typer(memory_app)

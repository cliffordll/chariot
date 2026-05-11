"""MemoryRepo: long-term memory entries, events, and links."""

from __future__ import annotations

import json
from typing import Any, cast

from sqlalchemy import delete as sa_delete
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.config import ConfigError
from chariot.database.models import MemoryEventRow, MemoryLinkRow, MemoryRow
from chariot.memory.policy import MemoryPolicy
from chariot.models.memory import MemoryEntry, MemoryEventEntry, MemoryLinkEntry


class MemoryRepo:
    """Data access for `memories`, `memory_events`, and `memory_links`."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_entries(
        self,
        *,
        kind: str | None = None,
        pinned: bool | None = None,
        archived: bool | None = False,
        conversation_id: str | None = None,
        provider_name: str | None = None,
        tag: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MemoryEntry]:
        stmt = select(MemoryRow)
        stmt = self._apply_entry_filters(
            stmt,
            kind=kind,
            pinned=pinned,
            archived=archived,
            conversation_id=conversation_id,
            provider_name=provider_name,
            tag=tag,
            search=search,
        )
        stmt = stmt.order_by(MemoryRow.is_pinned.desc(), MemoryRow.updated_at.desc(), MemoryRow.id.desc())
        stmt = stmt.limit(limit).offset(offset)
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._row_to_entry(row) for row in rows]

    async def get_entry(self, entry_id: str) -> MemoryEntry | None:
        row = await self.session.get(MemoryRow, entry_id)
        return self._row_to_entry(row) if row is not None else None

    async def create(
        self,
        *,
        kind: str,
        text: str,
        meta: dict[str, Any] | None = None,
        pinned: bool = False,
        archived: bool = False,
        links: list[dict[str, Any]] | None = None,
    ) -> MemoryEntry:
        self._require_non_empty(kind, "memory kind")
        self._require_non_empty(text, "memory text")
        row = MemoryRow(
            kind=kind,
            text=text,
            meta=self._serialize_json("meta", meta or {}),
            is_pinned=1 if pinned else 0,
            is_archived=1 if archived else 0,
        )
        self.session.add(row)
        await self.session.flush()
        await self._record_event(row.id, "created", {"kind": kind, "pinned": pinned, "archived": archived})
        await self._replace_links(row.id, links or [])
        await self.session.commit()
        await self.session.refresh(row)
        return self._row_to_entry(row)

    async def update(
        self,
        entry_id: str,
        *,
        kind: str | None = None,
        text: str | None = None,
        meta: dict[str, Any] | None = None,
        pinned: bool | None = None,
        archived: bool | None = None,
        links: list[dict[str, Any]] | None = None,
        event_type: str = "updated",
    ) -> MemoryEntry:
        row = await self._get_row(entry_id)
        changed: dict[str, Any] = {}
        if kind is not None:
            self._require_non_empty(kind, "memory kind")
            row.kind = kind
            changed["kind"] = kind
        if text is not None:
            self._require_non_empty(text, "memory text")
            row.text = text
            changed["text"] = text
        if meta is not None:
            row.meta = self._serialize_json("meta", meta)
            changed["meta"] = meta
        if pinned is not None:
            row.is_pinned = 1 if pinned else 0
            changed["pinned"] = pinned
        if archived is not None:
            row.is_archived = 1 if archived else 0
            changed["archived"] = archived
        if links is not None:
            await self._replace_links(row.id, links)
            changed["links"] = links
        await self._record_event(row.id, event_type, changed)
        await self.session.commit()
        await self.session.refresh(row)
        return self._row_to_entry(row)

    async def delete(self, entry_id: str) -> None:
        row = await self._get_row(entry_id)
        await self._record_event(row.id, "deleted", self._entry_payload(row))
        await self.session.execute(sa_delete(MemoryLinkRow).where(MemoryLinkRow.memory_id == row.id))
        await self.session.delete(row)
        await self.session.commit()

    async def pin(self, entry_id: str, pinned: bool = True) -> MemoryEntry:
        return await self.update(entry_id, pinned=pinned, event_type="pinned" if pinned else "unpinned")

    async def archive(self, entry_id: str, archived: bool = True) -> MemoryEntry:
        return await self.update(
            entry_id,
            archived=archived,
            event_type="archived" if archived else "restored",
        )

    async def list_events(
        self,
        *,
        memory_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MemoryEventEntry]:
        stmt = select(MemoryEventRow).order_by(MemoryEventRow.created_at.desc(), MemoryEventRow.id.desc())
        if memory_id is not None:
            stmt = stmt.where(MemoryEventRow.memory_id == memory_id)
        stmt = stmt.limit(limit).offset(offset)
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._event_to_entry(row) for row in rows]

    async def list_links(
        self,
        *,
        memory_id: str | None = None,
        link_type: str | None = None,
        link_value: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MemoryLinkEntry]:
        stmt = select(MemoryLinkRow).order_by(MemoryLinkRow.created_at.desc(), MemoryLinkRow.id.desc())
        if memory_id is not None:
            stmt = stmt.where(MemoryLinkRow.memory_id == memory_id)
        if link_type is not None:
            stmt = stmt.where(MemoryLinkRow.link_type == link_type)
        if link_value is not None:
            stmt = stmt.where(MemoryLinkRow.link_value == link_value)
        stmt = stmt.limit(limit).offset(offset)
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._link_to_entry(row) for row in rows]

    async def search_entries(self, query: str, *, limit: int = 50, offset: int = 0) -> list[MemoryEntry]:
        stmt = (
            select(MemoryRow)
            .where(MemoryRow.text.contains(query))
            .order_by(MemoryRow.is_pinned.desc(), MemoryRow.updated_at.desc(), MemoryRow.id.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._row_to_entry(row) for row in rows]

    async def list_relevant_entries(
        self,
        *,
        conversation_id: str | None = None,
        provider_name: str | None = None,
        tags: list[str] | None = None,
        limit: int = 8,
        policy: MemoryPolicy | None = None,
    ) -> list[MemoryEntry]:
        policy = (policy or MemoryPolicy()).bounded(max_items=limit)
        pinned_entries = await self.list_entries(pinned=True, archived=False, limit=limit)
        conversation_entries: list[MemoryEntry] = []
        provider_entries: list[MemoryEntry] = []
        tag_entries: list[MemoryEntry] = []
        if conversation_id is not None:
            conversation_entries = await self.list_entries(
                conversation_id=conversation_id,
                archived=False,
                limit=limit,
            )
        if provider_name is not None:
            provider_entries = await self.list_entries(
                provider_name=provider_name,
                archived=False,
                limit=limit,
            )
        for tag in tags or []:
            tag_entries.extend(await self.list_entries(tag=tag, archived=False, limit=limit))
        return policy.select(
            pinned=pinned_entries,
            conversation=conversation_entries,
            provider=provider_entries,
            tags=tag_entries,
        )

    async def _get_row(self, entry_id: str) -> MemoryRow:
        row = await self.session.get(MemoryRow, entry_id)
        if row is None:
            raise ConfigError(f"memory entry {entry_id!r} not found")
        return row

    def _apply_entry_filters(
        self,
        stmt,
        *,
        kind: str | None,
        pinned: bool | None,
        archived: bool | None,
        conversation_id: str | None,
        provider_name: str | None,
        tag: str | None,
        search: str | None,
    ):
        if kind is not None:
            stmt = stmt.where(MemoryRow.kind == kind)
        if pinned is not None:
            stmt = stmt.where(MemoryRow.is_pinned == (1 if pinned else 0))
        if archived is not None:
            stmt = stmt.where(MemoryRow.is_archived == (1 if archived else 0))
        if search:
            stmt = stmt.where(MemoryRow.text.contains(search))
        if conversation_id is not None:
            stmt = stmt.where(
                exists(
                    select(MemoryLinkRow.id).where(
                        MemoryLinkRow.memory_id == MemoryRow.id,
                        MemoryLinkRow.link_type == "conversation",
                        MemoryLinkRow.link_value == conversation_id,
                    )
                )
            )
        if provider_name is not None:
            stmt = stmt.where(
                exists(
                    select(MemoryLinkRow.id).where(
                        MemoryLinkRow.memory_id == MemoryRow.id,
                        MemoryLinkRow.link_type == "provider",
                        MemoryLinkRow.link_value == provider_name,
                    )
                )
            )
        if tag is not None:
            stmt = stmt.where(
                exists(
                    select(MemoryLinkRow.id).where(
                        MemoryLinkRow.memory_id == MemoryRow.id,
                        MemoryLinkRow.link_type == "tag",
                        MemoryLinkRow.link_value == tag,
                    )
                )
            )
        return stmt

    async def _replace_links(self, memory_id: str, links: list[dict[str, Any]]) -> None:
        await self.session.execute(sa_delete(MemoryLinkRow).where(MemoryLinkRow.memory_id == memory_id))
        for link in links:
            link_type = str(link.get("link_type") or link.get("type") or "").strip()
            link_value = str(link.get("link_value") or link.get("value") or "").strip()
            if not link_type or not link_value:
                raise ConfigError("memory link requires link_type and link_value")
            self.session.add(MemoryLinkRow(memory_id=memory_id, link_type=link_type, link_value=link_value))
        await self.session.flush()

    async def _record_event(self, memory_id: str, event_type: str, payload: dict[str, Any]) -> None:
        self.session.add(
            MemoryEventRow(
                memory_id=memory_id,
                event_type=event_type,
                payload=self._serialize_json("event payload", payload),
            )
        )
        await self.session.flush()

    @staticmethod
    def _serialize_json(label: str, data: Any) -> str:
        try:
            return json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            raise ConfigError(f"memory {label} not JSON serializable: {e}") from e

    @staticmethod
    def _deserialize_json(label: str, raw: str) -> dict[str, Any]:
        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ConfigError(f"memory {label} JSON broken: {e}") from e
        if not isinstance(data, dict):
            raise ConfigError(f"memory {label} JSON top level must be object")
        return cast(dict[str, Any], data)

    @classmethod
    def _row_to_entry(cls, row: MemoryRow) -> MemoryEntry:
        return MemoryEntry(
            id=row.id,
            kind=row.kind,
            text=row.text,
            meta=cls._deserialize_json("meta", row.meta),
            pinned=bool(getattr(row, "is_pinned", 0)),
            archived=bool(getattr(row, "is_archived", 0)),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @classmethod
    def _event_to_entry(cls, row: MemoryEventRow) -> MemoryEventEntry:
        return MemoryEventEntry(
            id=row.id,
            memory_id=row.memory_id,
            event_type=row.event_type,
            payload=cls._deserialize_json("event payload", row.payload),
            created_at=row.created_at,
        )

    @staticmethod
    def _link_to_entry(row: MemoryLinkRow) -> MemoryLinkEntry:
        return MemoryLinkEntry(
            id=row.id,
            memory_id=row.memory_id,
            link_type=row.link_type,
            link_value=row.link_value,
            created_at=row.created_at,
        )

    @staticmethod
    def _require_non_empty(value: str, label: str) -> None:
        if not value:
            raise ConfigError(f"{label} must be a non-empty string")

    @staticmethod
    def _entry_payload(row: MemoryRow) -> dict[str, Any]:
        return {
            "kind": row.kind,
            "text": row.text,
            "meta": row.meta,
            "pinned": bool(getattr(row, "is_pinned", 0)),
            "archived": bool(getattr(row, "is_archived", 0)),
        }

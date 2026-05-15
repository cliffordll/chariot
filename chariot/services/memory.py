"""Memory domain service.

Currently a thin wrapper around `MemoryRepo`. Future work will extract
business rules (auto-capture / retrieval policy / link validation 等)
from the repo into this layer; for now the service exists so callers can
depend on a stable domain-layer entry point.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, cast

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest
from chariot.memory.capture import MemoryCaptureService
from chariot.memory.policy import MemoryPolicy
from chariot.models.memory import MemoryEntry, MemoryEventEntry, MemoryLinkEntry
from chariot.repos.memory_repo import MemoryRepo
from chariot.services._session_proxy import SessionRepoProxy

if TYPE_CHECKING:
    from chariot.audit import AuditHookManager


class _SessionRuntime(Protocol):
    async def execute(self, *args: Any, **kwargs: Any) -> Any: ...

    async def commit(self) -> Any: ...


class MemoryService:
    def __init__(self, session_maker: object) -> None:
        self._runtime = session_maker
        self._repo = SessionRepoProxy(session_maker, MemoryRepo)

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
        return await self._repo.list_entries(
            kind=kind,
            pinned=pinned,
            archived=archived,
            conversation_id=conversation_id,
            provider_name=provider_name,
            tag=tag,
            search=search,
            limit=limit,
            offset=offset,
        )

    async def get_entry(self, entry_id: str) -> MemoryEntry | None:
        return await self._repo.get_entry(entry_id)

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
        return await self._repo.create(
            kind=kind,
            text=text,
            meta=meta,
            pinned=pinned,
            archived=archived,
            links=links,
        )

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
    ) -> MemoryEntry:
        return await self._repo.update(
            entry_id,
            kind=kind,
            text=text,
            meta=meta,
            pinned=pinned,
            archived=archived,
            links=links,
        )

    async def delete(self, entry_id: str) -> None:
        await self._repo.delete(entry_id)

    async def pin(self, entry_id: str, pinned: bool = True) -> MemoryEntry:
        return await self._repo.pin(entry_id, pinned=pinned)

    async def archive(self, entry_id: str, archived: bool = True) -> MemoryEntry:
        return await self._repo.archive(entry_id, archived=archived)

    async def list_events(
        self,
        *,
        memory_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MemoryEventEntry]:
        return await self._repo.list_events(memory_id=memory_id, limit=limit, offset=offset)

    async def list_links(
        self,
        *,
        memory_id: str | None = None,
        link_type: str | None = None,
        link_value: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MemoryLinkEntry]:
        return await self._repo.list_links(
            memory_id=memory_id,
            link_type=link_type,
            link_value=link_value,
            limit=limit,
            offset=offset,
        )

    async def search_entries(self, query: str, *, limit: int = 50, offset: int = 0) -> list[MemoryEntry]:
        return await self._repo.search_entries(query, limit=limit, offset=offset)

    async def list_relevant_entries(
        self,
        *,
        conversation_id: str | None = None,
        provider_name: str | None = None,
        tags: list[str] | None = None,
        limit: int = 8,
        policy: MemoryPolicy | None = None,
    ) -> list[MemoryEntry]:
        return await self._repo.list_relevant_entries(
            conversation_id=conversation_id,
            provider_name=provider_name,
            tags=tags,
            limit=limit,
            policy=policy,
        )

    async def list_relevant_entry_payloads(
        self,
        *,
        conversation_id: str | None = None,
        provider_name: str | None = None,
        tags: list[str] | None = None,
        limit: int = 8,
        policy: MemoryPolicy | None = None,
    ) -> list[dict[str, Any]] | None:
        entries = await self.list_relevant_entries(
            conversation_id=conversation_id,
            provider_name=provider_name,
            tags=tags,
            limit=limit,
            policy=policy,
        )
        payloads = [
            {
                "id": entry.id,
                "kind": entry.kind,
                "text": entry.text,
                "meta": entry.meta,
                "pinned": entry.pinned,
                "archived": entry.archived,
            }
            for entry in entries
        ]
        return payloads or None

    async def capture_turn(
        self,
        *,
        req: ChatRequest,
        provider_name: str,
        policy: MemoryPolicy,
        prompt_trace_id: str | None = None,
        context_trace_id: str | None = None,
        audit_hooks: AuditHookManager | None = None,
    ) -> list[MemoryEntry]:
        capture = MemoryCaptureService(self._concrete_repo(), audit_hooks=audit_hooks)
        return await capture.capture_turn(
            req=req,
            provider_name=provider_name,
            policy=policy,
            prompt_trace_id=prompt_trace_id,
            context_trace_id=context_trace_id,
        )

    async def capture_error(
        self,
        *,
        conversation_id: str | None,
        provider_name: str,
        error_event: ChatEvent,
        prompt_trace_id: str | None = None,
        context_trace_id: str | None = None,
        audit_hooks: AuditHookManager | None = None,
    ) -> list[MemoryEntry]:
        if error_event.error_type is None or error_event.error_message is None:
            return []
        capture = MemoryCaptureService(self._concrete_repo(), audit_hooks=audit_hooks)
        return await capture.capture_error(
            conversation_id=conversation_id,
            provider_name=provider_name,
            error_type=error_event.error_type,
            error_message=error_event.error_message,
            prompt_trace_id=prompt_trace_id,
            context_trace_id=context_trace_id,
        )

    def _concrete_repo(self) -> MemoryRepo:
        if hasattr(self._runtime, "execute") and hasattr(self._runtime, "commit"):
            return MemoryRepo(cast(Any, self._runtime))
        raise TypeError("MemoryService capture operations require a concrete session runtime")

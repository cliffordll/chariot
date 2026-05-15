"""Audit domain service."""

from __future__ import annotations

from chariot.repos.audit_repo import AuditEvent, AuditRepo
from chariot.services._session_proxy import SessionRepoProxy


class AuditService:
    def __init__(self, session_maker: object) -> None:
        self._repo = SessionRepoProxy(session_maker, AuditRepo)

    async def list_events(self, *, limit: int = 50) -> list[AuditEvent]:
        return await self._repo.list_events(limit=limit)

    async def get_event(self, event_id: str) -> AuditEvent | None:
        return await self._repo.get_event(event_id)

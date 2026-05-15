"""Sidecar audit API surface."""

from __future__ import annotations

from typing import Any

from chariot.repos.audit_repo import AuditEvent
from chariot.services.audit import AuditService
from chariot.sidecar.runtime import SidecarRuntime


class AuditApi:
    def __init__(self, runtime: SidecarRuntime) -> None:
        self._runtime = runtime

    async def list_events(self, *, limit: int = 50) -> list[dict[str, Any]]:
        events = await AuditService(self._runtime).list_events(limit=limit)
        return [self._event_to_dict(event) for event in events]

    async def get_event(self, event_id: str) -> dict[str, Any] | None:
        event = await AuditService(self._runtime).get_event(event_id)
        if event is None:
            return None
        return self._event_to_dict(event)

    @staticmethod
    def _event_to_dict(event: AuditEvent) -> dict[str, Any]:
        return {
            "id": event.id,
            "event_type": event.event_type,
            "status": event.status,
            "payload": event.payload,
            "created_at": event.created_at.isoformat(),
        }

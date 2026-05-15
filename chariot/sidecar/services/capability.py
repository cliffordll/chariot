"""Sidecar capability API surface."""

from __future__ import annotations

from typing import Any

from chariot.repos.capability_repo import CapabilityEntry
from chariot.services.capability import CapabilityService
from chariot.sidecar.runtime import SidecarRuntime


class CapabilityApi:
    def __init__(self, runtime: SidecarRuntime) -> None:
        self._runtime = runtime

    async def list_entries(self) -> list[dict[str, Any]]:
        entries = await CapabilityService(self._runtime).list_entries()
        return [self._entry_to_dict(entry) for entry in entries]

    async def set_enabled(self, name: str, enabled: bool) -> dict[str, Any]:
        entry = await CapabilityService(self._runtime).set_enabled(name, enabled)
        return self._entry_to_dict(entry)

    @staticmethod
    def _entry_to_dict(entry: CapabilityEntry) -> dict[str, Any]:
        return {
            "name": entry.name,
            "enabled": entry.enabled,
            "updated_at": entry.updated_at.isoformat(),
        }

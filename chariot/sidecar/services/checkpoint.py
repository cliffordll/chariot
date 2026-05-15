"""Sidecar checkpoint API surface."""

from __future__ import annotations

from typing import Any

from chariot.repos.checkpoint_repo import CheckpointEntry
from chariot.services.checkpoint import CheckpointService
from chariot.sidecar.runtime import SidecarRuntime


class CheckpointApi:
    def __init__(self, runtime: SidecarRuntime) -> None:
        self._runtime = runtime

    async def list_entries(self) -> list[dict[str, Any]]:
        entries = await CheckpointService(self._runtime).list_entries()
        return [self._entry_to_dict(entry) for entry in entries]

    async def get_entry(self, checkpoint_id: str) -> dict[str, Any] | None:
        entry = await CheckpointService(self._runtime).get_entry(checkpoint_id)
        if entry is None:
            return None
        return self._entry_to_dict(entry)

    @staticmethod
    def _entry_to_dict(entry: CheckpointEntry) -> dict[str, Any]:
        return {
            "id": entry.id,
            "name": entry.name,
            "kind": entry.kind,
            "target": entry.target,
            "payload": entry.payload,
            "created_at": entry.created_at.isoformat(),
        }

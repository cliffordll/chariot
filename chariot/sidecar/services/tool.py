"""Tool service for sidecar admin methods."""

from __future__ import annotations

from typing import Any

from chariot.agent.config import ToolEntry
from chariot.repos.tool_repo import ToolRepo
from chariot.sidecar.runtime import SidecarRuntime


class ToolService:
    def __init__(self, runtime: SidecarRuntime) -> None:
        self._runtime = runtime

    async def list_entries(self, session: Any) -> list[dict[str, Any]]:
        entries = await ToolRepo(session).list_entries()
        return [self.serialize(entry) for entry in entries]

    async def set_enabled(
        self, session: Any, *, name: str, enabled: bool
    ) -> dict[str, Any]:
        entry = await ToolRepo(session).update(name, enabled=enabled)
        await self._runtime.reload()
        return self.serialize(entry)

    async def update_options(
        self, session: Any, *, name: str, options: dict[str, Any]
    ) -> dict[str, Any]:
        entry = await ToolRepo(session).update(name, options=options)
        await self._runtime.reload()
        return self.serialize(entry)

    @staticmethod
    def serialize(entry: ToolEntry) -> dict[str, Any]:
        return {
            "name": entry.name,
            "type": entry.type,
            "enabled": entry.enabled,
            "options": entry.options,
        }

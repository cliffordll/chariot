"""Tool domain service.

Currently a thin wrapper around `ToolRepo`. Future work will extract
business rules (toolset / policy / activation routing 等)from the repo
into this layer; for now the service exists so callers can depend on a
stable domain-layer entry point.
"""

from __future__ import annotations

from typing import Any

from chariot.models.tool import ToolEntry
from chariot.repos.tool_repo import ToolRepo


class ToolService:
    def __init__(self, repo: ToolRepo) -> None:
        self._repo = repo

    async def list_entries(self) -> list[ToolEntry]:
        return await self._repo.list_entries()

    async def list_enabled(self) -> list[ToolEntry]:
        return await self._repo.list_enabled()

    async def get_entry(self, name: str) -> ToolEntry | None:
        return await self._repo.get_entry(name)

    async def update(
        self,
        name: str,
        *,
        enabled: bool | None = None,
        options: dict[str, Any] | None = None,
    ) -> ToolEntry:
        return await self._repo.update(name, enabled=enabled, options=options)

    async def sync_builtin_tools(self) -> None:
        await self._repo.sync_builtin_tools()

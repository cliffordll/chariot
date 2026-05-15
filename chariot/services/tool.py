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
from chariot.services._session_proxy import SessionRepoProxy


class ToolService:
    def __init__(self, session_maker: object) -> None:
        self._repo = SessionRepoProxy(session_maker, ToolRepo)

    # ---- read ----

    async def list_entries(self) -> list[ToolEntry]:
        return await self._repo.list_entries()

    async def list_enabled(self) -> list[ToolEntry]:
        return await self._repo.list_enabled()

    async def get_entry(self, name: str) -> ToolEntry | None:
        return await self._repo.get_entry(name)

    # ---- write ----

    async def create(
        self,
        *,
        name: str,
        type: str,
        enabled: bool = True,
        options: dict[str, Any],
        source: str = "custom",
        description: str = "",
        custom_type: str | None = None,
    ) -> ToolEntry:
        return await self._repo.create(
            name=name,
            type=type,
            enabled=enabled,
            options=options,
            source=source,
            description=description,
            custom_type=custom_type,
        )

    async def update(
        self,
        name: str,
        *,
        enabled: bool | None = None,
        options: dict[str, Any] | None = None,
    ) -> ToolEntry:
        return await self._repo.update(name, enabled=enabled, options=options)

    async def update_full(
        self,
        name: str,
        *,
        enabled: bool | None = None,
        options: dict[str, Any] | None = None,
        description: str | None = None,
    ) -> ToolEntry:
        return await self._repo.update_full(
            name,
            enabled=enabled,
            options=options,
            description=description,
        )

    async def delete(self, name: str) -> None:
        await self._repo.delete(name)

    async def sync_builtin_tools(self) -> None:
        await self._repo.sync_builtin_tools()

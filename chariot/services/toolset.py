"""Toolset domain service.

Currently a thin wrapper around `ToolsetRepo`. Future work will extract
business rules (membership validation against active tools, apply
semantics, profile binding policy)from the repo into this layer; for now
the service exists so callers can depend on a stable domain-layer entry
point.

设计参考 docs/tool-profile-design.md。
"""

from __future__ import annotations

from typing import Any

from chariot.models.toolset import Toolset
from chariot.repos.toolset_repo import ToolsetRepo
from chariot.services._session_proxy import SessionRepoProxy


class ToolsetService:
    def __init__(self, session_maker: object) -> None:
        self._repo = SessionRepoProxy(session_maker, ToolsetRepo)

    async def list_entries(self) -> list[Toolset]:
        return await self._repo.list_entries()

    async def get_entry(self, ref: str) -> Toolset | None:
        return await self._repo.get_entry(ref)

    async def list_members(self, ref: str) -> list[str]:
        return await self._repo.list_members(ref)

    async def create(
        self,
        *,
        name: str,
        description: str | None = None,
        members: list[str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Toolset:
        return await self._repo.create(
            name=name,
            description=description,
            members=members,
            meta=meta,
        )

    async def update(
        self,
        name: str,
        *,
        description: str | None = None,
        meta: dict[str, Any] | None = None,
        members: list[str] | None = None,
    ) -> Toolset:
        return await self._repo.update(
            name,
            description=description,
            meta=meta,
            members=members,
        )

    async def delete(self, name: str) -> None:
        await self._repo.delete(name)

    async def add_member(self, name: str, tool_name: str) -> Toolset:
        return await self._repo.add_member(name, tool_name)

    async def remove_member(self, name: str, tool_name: str) -> Toolset:
        return await self._repo.remove_member(name, tool_name)

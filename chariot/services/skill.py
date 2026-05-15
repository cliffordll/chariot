"""Skill domain service.

Wraps `SkillRepo` to provide a stable domain-layer entry point for
skill CRUD operations. Audit logging remains at the Surface layer
calling `audit_hooks.record_skill_store`.
"""

from __future__ import annotations

from typing import Any

from chariot.repos.skill_repo import SkillEntry, SkillRepo
from chariot.services._session_proxy import SessionRepoProxy

__all__ = ["SkillService"]


class SkillService:
    def __init__(self, session_maker: object) -> None:
        self._repo = SessionRepoProxy(session_maker, SkillRepo)

    # ---- read ----

    async def list_entries(self) -> list[SkillEntry]:
        return await self._repo.list_entries()

    async def get_entry(self, entry_id: str) -> SkillEntry | None:
        return await self._repo.get_entry(entry_id)

    async def get_by_name(self, name: str) -> SkillEntry | None:
        return await self._repo.get_by_name(name)

    # ---- write ----

    async def create(
        self,
        *,
        name: str,
        description: str | None = None,
        content: str = "",
        enabled: bool = True,
        meta: dict[str, Any] | None = None,
    ) -> SkillEntry:
        return await self._repo.create(
            name=name,
            description=description,
            content=content,
            enabled=enabled,
            meta=meta,
        )

    async def set_enabled(self, entry_id: str, enabled: bool) -> SkillEntry:
        return await self._repo.set_enabled(entry_id, enabled)

    async def delete(self, entry_id: str) -> bool:
        return await self._repo.delete(entry_id)

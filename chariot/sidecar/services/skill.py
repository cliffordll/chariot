"""Sidecar skill API surface."""

from __future__ import annotations

from typing import Any

from chariot.repos.skill_repo import SkillEntry
from chariot.services.skill import SkillService
from chariot.sidecar.runtime import SidecarRuntime


class SkillApi:
    def __init__(self, runtime: SidecarRuntime) -> None:
        self._runtime = runtime

    async def list_entries(self) -> list[SkillEntry]:
        return await SkillService(self._runtime).list_entries()

    async def get_entry(self, entry_id: str) -> SkillEntry | None:
        return await SkillService(self._runtime).get_entry(entry_id)

    async def get_by_name(self, name: str) -> SkillEntry | None:
        return await SkillService(self._runtime).get_by_name(name)

    async def create(
        self,
        *,
        name: str,
        description: str | None = None,
        content: str = "",
        enabled: bool = True,
        meta: dict[str, Any] | None = None,
    ) -> SkillEntry:
        return await SkillService(self._runtime).create(
            name=name,
            description=description,
            content=content,
            enabled=enabled,
            meta=meta,
        )

    async def set_enabled(self, entry_id: str, enabled: bool) -> SkillEntry:
        return await SkillService(self._runtime).set_enabled(entry_id, enabled)

    async def delete(self, entry_id: str) -> bool:
        return await SkillService(self._runtime).delete(entry_id)

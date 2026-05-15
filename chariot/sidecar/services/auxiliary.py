"""Sidecar auxiliary client API surface."""

from __future__ import annotations

from typing import Any

from chariot.models.agent import ClearableStr
from chariot.models.auxiliary import AuxiliaryClientEntry
from chariot.services.auxiliary import AuxiliaryService
from chariot.sidecar.runtime import SidecarRuntime


class AuxiliaryApi:
    def __init__(self, runtime: SidecarRuntime) -> None:
        self._runtime = runtime

    async def list_entries(self) -> list[AuxiliaryClientEntry]:
        return await AuxiliaryService(self._runtime).list_entries()

    async def get_entry(self, name: str) -> AuxiliaryClientEntry | None:
        return await AuxiliaryService(self._runtime).get_entry(name)

    async def create(
        self,
        *,
        name: str,
        provider_entry: str,
        model: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> AuxiliaryClientEntry:
        return await AuxiliaryService(self._runtime).create(
            name=name,
            provider_entry=provider_entry,
            model=model,
            params=params,
        )

    async def update(
        self,
        name: str,
        *,
        provider_entry: str | None = None,
        model: ClearableStr = None,
        params: dict[str, Any] | None = None,
    ) -> AuxiliaryClientEntry:
        return await AuxiliaryService(self._runtime).update(
            name,
            provider_entry=provider_entry,
            model=model,
            params=params,
        )

    async def delete(self, name: str) -> None:
        await AuxiliaryService(self._runtime).delete(name)

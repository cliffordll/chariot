"""Prompt domain service.

Currently a thin wrapper around `PromptRepo`. Future work will extract
business rules (activate_bundle / record_trace / seed_if_empty etc.) from
the repo into this layer; for now the service exists so callers can depend
on a stable domain-layer entry point.
"""

from __future__ import annotations

from chariot.models.prompt import PromptBundleEntry, PromptTraceEntry, PromptVersionEntry
from chariot.repos.prompt_repo import PromptRepo


class PromptService:
    def __init__(self, repo: PromptRepo) -> None:
        self._repo = repo

    async def list_bundles(self) -> list[PromptBundleEntry]:
        return await self._repo.list_bundles()

    async def get_bundle(self, name: str) -> PromptBundleEntry | None:
        return await self._repo.get_bundle(name)

    async def get_active_bundle(self) -> PromptBundleEntry | None:
        return await self._repo.get_active_bundle()

    async def get_active_version(self, bundle_name: str | None = None) -> PromptVersionEntry | None:
        return await self._repo.get_active_version(bundle_name)

    async def list_versions(self, bundle_name: str) -> list[PromptVersionEntry]:
        return await self._repo.list_versions(bundle_name)

    async def get_version(self, bundle_name: str, version: str) -> PromptVersionEntry | None:
        return await self._repo.get_version(bundle_name, version)

    async def get_trace(self, trace_id: str) -> PromptTraceEntry | None:
        return await self._repo.get_trace(trace_id)

    async def activate_bundle(self, name: str) -> PromptBundleEntry:
        return await self._repo.activate_bundle(name)

    async def activate_version(self, bundle_name: str, version: str) -> PromptVersionEntry:
        return await self._repo.activate_version(bundle_name, version)

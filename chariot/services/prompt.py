"""Prompt domain service.

Wraps `PromptRepo` to provide a stable domain-layer entry point for
prompt bundle / version / trace operations.
"""

from __future__ import annotations

from typing import Any

from chariot.agent.chat_request import ChatRequest
from chariot.models.prompt import PromptBundleEntry, PromptTraceEntry, PromptVersionEntry
from chariot.repos.prompt_repo import PromptRepo
from chariot.services._session_proxy import SessionRepoProxy

_MISSING = object()


class PromptService:
    def __init__(self, session_maker: object) -> None:
        self._repo = SessionRepoProxy(session_maker, PromptRepo)

    # ---- read ----

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

    async def list_traces(
        self,
        *,
        bundle_name: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PromptTraceEntry]:
        if bundle_name:
            return await self._repo.list_traces_by_bundle(bundle_name, limit=limit, offset=offset)
        return await self._repo.list_traces(limit=limit, offset=offset)

    async def list_trace_versions(self) -> list[PromptVersionEntry]:
        return await self._repo.list_trace_versions()

    # ---- write ----

    async def seed_if_empty(self) -> None:
        await self._repo.seed_if_empty()

    async def create_bundle(
        self,
        name: str,
        *,
        description: str | None = None,
        layers: list[dict[str, Any]] | None = None,
    ) -> PromptVersionEntry:
        return await self._repo.create_bundle(name, description=description, layers=layers)

    async def update_bundle(
        self,
        name: str,
        *,
        description: str | None = None,
        layers: list[dict[str, Any]] | None = None,
    ) -> PromptVersionEntry:
        kwargs: dict[str, Any] = {}
        if description is not None:
            kwargs["description"] = description
        if layers is not None:
            kwargs["layers"] = layers
        return await self._repo.update_bundle(name, **kwargs)

    async def activate_bundle(self, name: str) -> PromptBundleEntry:
        return await self._repo.activate_bundle(name)

    async def activate_version(self, bundle_name: str, version: str) -> PromptVersionEntry:
        return await self._repo.activate_version(bundle_name, version)

    async def record_trace(
        self,
        req: ChatRequest,
        *,
        provider_name: str,
        model: str | None,
        bundle_name: str | None = None,
        version: str | None = None,
        memory_entries: list[dict[str, Any]] | None = None,
        memory_policy: dict[str, Any] | None = None,
    ) -> PromptTraceEntry:
        return await self._repo.record_trace(
            req,
            provider_name=provider_name,
            model=model,
            bundle_name=bundle_name,
            version=version,
            memory_entries=memory_entries,
            memory_policy=memory_policy,
        )

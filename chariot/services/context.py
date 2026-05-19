"""Context domain service."""

from __future__ import annotations

from typing import Any

from chariot.models.context import (
    ContextBundleEntry,
    ContextSnapshot,
    ContextSnapshotEntry,
    ContextTraceEntry,
    ContextVersionEntry,
)
from chariot.repos.context_repo import ContextRepo
from chariot.services._session_proxy import SessionRepoProxy


class ContextService:
    def __init__(self, session_maker: object) -> None:
        self._repo = SessionRepoProxy(session_maker, ContextRepo)

    async def seed_if_empty(self) -> None:
        await self._repo.seed_if_empty()

    async def list_bundles(self) -> list[ContextBundleEntry]:
        return await self._repo.list_bundles()

    async def get_bundle(self, ref: str) -> ContextBundleEntry | None:
        return await self._repo.get_bundle(ref)

    async def get_active_bundle(self) -> ContextBundleEntry | None:
        return await self._repo.get_active_bundle()

    async def resolve_bundle(self, *, bundle_name: str | None = None) -> ContextBundleEntry | None:
        if bundle_name is not None:
            bundle = await self._repo.get_bundle(bundle_name)
            if bundle is not None:
                return bundle
        return await self._repo.get_active_bundle()

    async def get_active_version(self, bundle_name: str | None = None) -> ContextVersionEntry | None:
        return await self._repo.get_active_version(bundle_name)

    async def list_versions(self, bundle_name: str) -> list[ContextVersionEntry]:
        return await self._repo.list_versions(bundle_name)

    async def get_version(self, bundle_name: str, version: str) -> ContextVersionEntry | None:
        return await self._repo.get_version(bundle_name, version)

    async def create_bundle(
        self,
        name: str,
        *,
        description: str | None = None,
        spec: dict[str, Any] | None = None,
    ) -> ContextVersionEntry:
        return await self._repo.create_bundle(name, description=description, spec=spec)

    async def update_bundle(
        self,
        name: str,
        *,
        description: str | None = None,
        spec: dict[str, Any] | None = None,
    ) -> ContextVersionEntry:
        kwargs: dict[str, Any] = {}
        if description is not None:
            kwargs["description"] = description
        if spec is not None:
            kwargs["spec"] = spec
        return await self._repo.update_bundle(name, **kwargs)

    async def rename_bundle(self, name: str, new_name: str) -> ContextBundleEntry:
        return await self._repo.rename_bundle(name, new_name=new_name)

    async def activate_bundle(self, name: str) -> ContextBundleEntry:
        return await self._repo.activate_bundle(name)

    async def activate_version(self, bundle_name: str, version: str) -> ContextVersionEntry:
        return await self._repo.activate_version(bundle_name, version)

    async def list_snapshots(
        self,
        *,
        conversation_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ContextSnapshotEntry]:
        return await self._repo.list_snapshots(
            conversation_id=conversation_id,
            limit=limit,
            offset=offset,
        )

    async def get_snapshot(self, snapshot_id: str) -> ContextSnapshotEntry | None:
        return await self._repo.get_snapshot(snapshot_id)

    async def list_traces(
        self,
        *,
        conversation_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ContextTraceEntry]:
        return await self._repo.list_traces(
            conversation_id=conversation_id,
            limit=limit,
            offset=offset,
        )

    async def get_trace(self, trace_id: str) -> ContextTraceEntry | None:
        return await self._repo.get_trace(trace_id)

    async def get_trace_by_snapshot_id(self, snapshot_id: str) -> ContextTraceEntry | None:
        return await self._repo.get_trace_by_snapshot_id(snapshot_id)

    async def record_snapshot(self, snapshot: ContextSnapshot) -> ContextSnapshotEntry:
        return await self._repo.record_snapshot(snapshot)

    async def record_trace(
        self,
        snapshot_id: str,
        *,
        prompt_trace_id: str | None = None,
        policy_name: str = "default_context_policy",
        bundle_id: str | None = None,
        version_id: str | None = None,
    ) -> ContextTraceEntry:
        return await self._repo.record_trace(
            snapshot_id,
            prompt_trace_id=prompt_trace_id,
            policy_name=policy_name,
            bundle_id=bundle_id,
            version_id=version_id,
        )

    async def inspect_context(self, context_id: str) -> dict[str, Any] | None:
        return await self._repo.inspect_context(context_id)

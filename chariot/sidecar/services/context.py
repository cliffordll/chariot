"""Context API surface for sidecar context-query methods."""

from __future__ import annotations

from typing import Any

from chariot.models.context import (
    ContextBundleEntry,
    ContextSnapshotEntry,
    ContextTraceEntry,
    ContextVersionEntry,
)
from chariot.rpc.jsonrpc import JsonRpcServer, RpcError
from chariot.services.context import ContextService
from chariot.sidecar.runtime import SidecarRuntime


class ContextApi:
    def __init__(self, runtime: SidecarRuntime) -> None:
        self._runtime = runtime

    async def list_bundles(self) -> list[dict[str, Any]]:
        entries = await ContextService(self._runtime).list_bundles()
        return [self._bundle_to_dict(entry) for entry in entries]

    async def get_bundle(self, *, name: str) -> dict[str, Any]:
        service = ContextService(self._runtime)
        bundle = await service.get_bundle(name)
        if bundle is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"context bundle {name!r} not found")
        versions = await service.list_versions(name)
        return {
            **self._bundle_to_dict(bundle),
            "versions": [self._version_to_dict(version) for version in versions],
        }

    async def list_versions(self, *, bundle_name: str) -> list[dict[str, Any]]:
        entries = await ContextService(self._runtime).list_versions(bundle_name)
        return [self._version_to_dict(entry) for entry in entries]

    async def get_version(self, *, bundle_name: str, version: str) -> dict[str, Any]:
        entry = await ContextService(self._runtime).get_version(bundle_name, version)
        if entry is None:
            raise RpcError(
                JsonRpcServer.ERR_NOT_FOUND,
                f"context version {bundle_name!r}:{version!r} not found",
            )
        return self._version_to_dict(entry)

    async def add_bundle(
        self,
        *,
        name: str,
        description: str | None,
        spec: dict[str, Any] | None,
    ) -> dict[str, Any]:
        service = ContextService(self._runtime)
        version = await service.create_bundle(name, description=description, spec=spec)
        bundle = await service.get_bundle(name)
        if bundle is None:
            raise RpcError(JsonRpcServer.ERR_INTERNAL, f"context bundle {name!r} not found after create")
        return {"bundle": self._bundle_to_dict(bundle), "version": self._version_to_dict(version)}

    async def update_bundle(
        self,
        *,
        name: str,
        rename: str | None = None,
        description: str | None = None,
        description_set: bool = False,
        spec: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        service = ContextService(self._runtime)
        if rename is not None:
            bundle = await service.rename_bundle(name, rename)
            name = bundle.id
        kwargs: dict[str, Any] = {}
        if description_set:
            kwargs["description"] = description
        if spec is not None:
            kwargs["spec"] = spec
        version = await service.update_bundle(name, **kwargs)
        bundle = await service.get_bundle(name)
        if bundle is None:
            raise RpcError(JsonRpcServer.ERR_INTERNAL, f"context bundle {name!r} not found after update")
        return {"bundle": self._bundle_to_dict(bundle), "version": self._version_to_dict(version)}

    async def activate_bundle(self, *, name: str, version: str | None = None) -> dict[str, Any]:
        service = ContextService(self._runtime)
        if version is None:
            bundle = await service.activate_bundle(name)
            active_version = await service.get_active_version(name)
            return {
                "bundle": self._bundle_to_dict(bundle),
                "version": self._version_to_dict(active_version) if active_version is not None else None,
            }
        version_entry = await service.activate_version(name, version)
        bundle = await service.get_bundle(name)
        if bundle is None:
            raise RpcError(JsonRpcServer.ERR_INTERNAL, f"context bundle {name!r} not found after activate")
        return {"bundle": self._bundle_to_dict(bundle), "version": self._version_to_dict(version_entry)}

    async def list_snapshots(
        self,
        *,
        conversation_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        entries = await ContextService(self._runtime).list_snapshots(
            conversation_id=conversation_id,
            limit=limit,
            offset=offset,
        )
        return [self._snapshot_to_dict(entry) for entry in entries]

    async def get_snapshot(self, *, snapshot_id: str) -> dict[str, Any]:
        entry = await ContextService(self._runtime).get_snapshot(snapshot_id)
        if entry is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"context snapshot {snapshot_id!r} not found")
        return self._snapshot_to_dict(entry)

    async def list_traces(
        self,
        *,
        conversation_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        entries = await ContextService(self._runtime).list_traces(
            conversation_id=conversation_id,
            limit=limit,
            offset=offset,
        )
        return [self._trace_to_dict(entry) for entry in entries]

    async def inspect_context(self, *, context_id: str) -> dict[str, Any]:
        entry = await ContextService(self._runtime).inspect_context(context_id)
        if entry is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"context {context_id!r} not found")
        return entry

    @staticmethod
    def _snapshot_to_dict(entry: ContextSnapshotEntry) -> dict[str, Any]:
        return {
            "id": entry.id,
            "conversation_id": entry.conversation_id,
            "provider_id": entry.provider_id,
            "provider_snapshot": entry.provider_snapshot,
            "model": entry.model,
            "request": entry.request,
            "slices": entry.slices,
            "source_refs": entry.source_refs,
            "context_size": entry.context_size,
            "created_at": entry.created_at.isoformat(),
        }

    @staticmethod
    def _trace_to_dict(entry: ContextTraceEntry) -> dict[str, Any]:
        return {
            "id": entry.id,
            "snapshot_id": entry.snapshot_id,
            "bundle_id": entry.bundle_id,
            "bundle_name": entry.bundle_name,
            "version_id": entry.version_id,
            "version": entry.version,
            "conversation_id": entry.conversation_id,
            "provider_id": entry.provider_id,
            "provider_snapshot": entry.provider_snapshot,
            "model": entry.model,
            "prompt_trace_id": entry.prompt_trace_id,
            "policy": entry.policy,
            "selected_refs": entry.selected_refs,
            "created_at": entry.created_at.isoformat(),
        }

    @staticmethod
    def _bundle_to_dict(entry: ContextBundleEntry) -> dict[str, Any]:
        return {
            "id": entry.id,
            "name": entry.name,
            "description": entry.description,
            "is_active": entry.is_active,
            "version_count": entry.version_count,
            "active_version": entry.active_version,
            "created_at": entry.created_at.isoformat(),
            "updated_at": entry.updated_at.isoformat(),
        }

    @staticmethod
    def _version_to_dict(entry: ContextVersionEntry) -> dict[str, Any]:
        return {
            "id": entry.id,
            "bundle_id": entry.bundle_id,
            "bundle_name": entry.bundle_name,
            "version": entry.version,
            "spec": entry.spec,
            "is_active": entry.is_active,
            "created_at": entry.created_at.isoformat(),
            "updated_at": entry.updated_at.isoformat(),
        }

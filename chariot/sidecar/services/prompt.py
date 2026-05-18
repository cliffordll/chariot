"""Prompt API surface for sidecar prompt-query methods."""

from __future__ import annotations

from typing import Any

from chariot.models.prompt import PromptBundleEntry, PromptTraceEntry, PromptVersionEntry
from chariot.rpc.jsonrpc import JsonRpcServer, RpcError
from chariot.services.prompt import PromptService
from chariot.sidecar.runtime import SidecarRuntime


class PromptApi:
    def __init__(self, runtime: SidecarRuntime) -> None:
        self._runtime = runtime

    async def list_bundles(self) -> list[dict[str, Any]]:
        entries = await PromptService(self._runtime).list_bundles()
        return [self._bundle_to_dict(entry) for entry in entries]

    async def get_bundle(self, *, name: str) -> dict[str, Any]:
        service = PromptService(self._runtime)
        bundle = await service.get_bundle(name)
        if bundle is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"prompt bundle {name!r} not found")
        versions = await service.list_versions(name)
        return {
            **self._bundle_to_dict(bundle),
            "versions": [self._version_to_dict(version) for version in versions],
        }

    async def list_versions(self, *, bundle_name: str) -> list[dict[str, Any]]:
        entries = await PromptService(self._runtime).list_versions(bundle_name)
        return [self._version_to_dict(entry) for entry in entries]

    async def get_version(
        self,
        *,
        bundle_name: str,
        version: str,
    ) -> dict[str, Any]:
        entry = await PromptService(self._runtime).get_version(bundle_name, version)
        if entry is None:
            raise RpcError(
                JsonRpcServer.ERR_NOT_FOUND,
                f"prompt version {bundle_name!r}:{version!r} not found",
            )
        return self._version_to_dict(entry)

    async def list_traces(
        self,
        *,
        bundle_name: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        entries = await PromptService(self._runtime).list_traces(
            bundle_name=bundle_name,
            limit=limit,
            offset=offset,
        )
        return [self._trace_to_dict(entry) for entry in entries]

    async def inspect_trace(self, *, trace_id: str) -> dict[str, Any]:
        trace = await PromptService(self._runtime).get_trace(trace_id)
        if trace is None:
            raise RpcError(
                JsonRpcServer.ERR_NOT_FOUND,
                f"prompt trace {trace_id!r} not found",
            )
        return self._trace_to_dict(trace)

    async def add_bundle(
        self,
        *,
        name: str,
        description: str | None,
        layers: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        service = PromptService(self._runtime)
        version = await service.create_bundle(name, description=description, layers=layers)
        bundle = await service.get_bundle(name)
        if bundle is None:
            raise RpcError(JsonRpcServer.ERR_INTERNAL, f"prompt bundle {name!r} not found after create")
        return {
            "bundle": self._bundle_to_dict(bundle),
            "version": self._version_to_dict(version),
        }

    async def update_bundle(
        self,
        *,
        name: str,
        rename: str | None = None,
        description: str | None = None,
        description_set: bool = False,
        layers: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        service = PromptService(self._runtime)
        if rename is not None:
            bundle = await service.rename_bundle(name, rename)
            name = bundle.id
        kwargs: dict[str, Any] = {}
        if description_set:
            kwargs["description"] = description
        if layers is not None:
            kwargs["layers"] = layers
        version = await service.update_bundle(name, **kwargs)
        bundle = await service.get_bundle(name)
        if bundle is None:
            raise RpcError(JsonRpcServer.ERR_INTERNAL, f"prompt bundle {name!r} not found after update")
        return {
            "bundle": self._bundle_to_dict(bundle),
            "version": self._version_to_dict(version),
        }

    async def activate_bundle(
        self,
        *,
        name: str,
        version: str | None = None,
    ) -> dict[str, Any]:
        service = PromptService(self._runtime)
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
            raise RpcError(JsonRpcServer.ERR_INTERNAL, f"prompt bundle {name!r} not found after activate")
        return {
            "bundle": self._bundle_to_dict(bundle),
            "version": self._version_to_dict(version_entry),
        }

    @staticmethod
    def _bundle_to_dict(entry: PromptBundleEntry) -> dict[str, Any]:
        return {
            "id": entry.id,
            "name": entry.name,
            "description": entry.description,
            "layers": entry.layers,
            "is_active": entry.is_active,
            "version_count": entry.version_count,
            "active_version": entry.active_version,
            "created_at": entry.created_at.isoformat(),
            "updated_at": entry.updated_at.isoformat(),
        }

    @staticmethod
    def _version_to_dict(entry: PromptVersionEntry) -> dict[str, Any]:
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

    @staticmethod
    def _trace_to_dict(entry: PromptTraceEntry) -> dict[str, Any]:
        return {
            "id": entry.id,
            "bundle_id": entry.bundle_id,
            "bundle_name": entry.bundle_name,
            "version_id": entry.version_id,
            "version": entry.version,
            "conversation_id": entry.conversation_id,
            "provider_id": entry.provider_id,
            "provider_snapshot": entry.provider_snapshot,
            "model": entry.model,
            "request": entry.request,
            "source_refs": entry.source_refs,
            "prompt_size": entry.prompt_size,
            "created_at": entry.created_at.isoformat(),
        }

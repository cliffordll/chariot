"""Prompt API surface for sidecar prompt-query methods."""

from __future__ import annotations

from typing import Any

from chariot.models.prompt import PromptBundleEntry, PromptTraceEntry, PromptVersionEntry
from chariot.repos.prompt_repo import PromptRepo
from chariot.rpc.jsonrpc import JsonRpcServer, RpcError
from chariot.sidecar.runtime import SidecarRuntime


class PromptApi:
    def __init__(self, runtime: SidecarRuntime) -> None:
        self._runtime = runtime

    async def list_bundles(self, session: Any) -> list[dict[str, Any]]:
        entries = await PromptRepo(session).list_bundles()
        return [self._bundle_to_dict(entry) for entry in entries]

    async def get_bundle(self, session: Any, *, name: str) -> dict[str, Any]:
        repo = PromptRepo(session)
        bundle = await repo.get_bundle(name)
        if bundle is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"prompt bundle {name!r} not found")
        versions = await repo.list_versions(name)
        return {
            **self._bundle_to_dict(bundle),
            "versions": [self._version_to_dict(version) for version in versions],
        }

    async def list_versions(self, session: Any, *, bundle_name: str) -> list[dict[str, Any]]:
        entries = await PromptRepo(session).list_versions(bundle_name)
        return [self._version_to_dict(entry) for entry in entries]

    async def get_version(self, session: Any, *, bundle_name: str, version: str) -> dict[str, Any]:
        repo = PromptRepo(session)
        entry = await repo.get_version(bundle_name, version)
        if entry is None:
            raise RpcError(
                JsonRpcServer.ERR_NOT_FOUND,
                f"prompt version {bundle_name!r}:{version!r} not found",
            )
        return self._version_to_dict(entry)

    async def list_traces(
        self,
        session: Any,
        *,
        bundle_name: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        repo = PromptRepo(session)
        if bundle_name:
            entries = await repo.list_traces_by_bundle(bundle_name, limit=limit, offset=offset)
        else:
            entries = await repo.list_traces(limit=limit, offset=offset)
        return [self._trace_to_dict(entry) for entry in entries]

    async def inspect_trace(self, session: Any, *, trace_id: str) -> dict[str, Any]:
        trace = await PromptRepo(session).get_trace(trace_id)
        if trace is None:
            raise RpcError(
                JsonRpcServer.ERR_NOT_FOUND,
                f"prompt trace {trace_id!r} not found",
            )
        return self._trace_to_dict(trace)

    async def add_bundle(
        self,
        session: Any,
        *,
        name: str,
        description: str | None,
        layers: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        repo = PromptRepo(session)
        version = await repo.create_bundle(name, description=description, layers=layers)
        bundle = await repo.get_bundle(name)
        if bundle is None:
            raise RpcError(JsonRpcServer.ERR_INTERNAL, f"prompt bundle {name!r} not found after create")
        return {
            "bundle": self._bundle_to_dict(bundle),
            "version": self._version_to_dict(version),
        }

    async def update_bundle(
        self,
        session: Any,
        *,
        name: str,
        description: str | None = None,
        description_set: bool = False,
        layers: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        repo = PromptRepo(session)
        kwargs: dict[str, Any] = {}
        if description_set:
            kwargs["description"] = description
        if layers is not None:
            kwargs["layers"] = layers
        version = await repo.update_bundle(name, **kwargs)
        bundle = await repo.get_bundle(name)
        if bundle is None:
            raise RpcError(JsonRpcServer.ERR_INTERNAL, f"prompt bundle {name!r} not found after update")
        return {
            "bundle": self._bundle_to_dict(bundle),
            "version": self._version_to_dict(version),
        }

    async def activate_bundle(
        self,
        session: Any,
        *,
        name: str,
        version: str | None = None,
    ) -> dict[str, Any]:
        repo = PromptRepo(session)
        if version is None:
            bundle = await repo.activate_bundle(name)
            active_version = await repo.get_active_version(name)
            return {
                "bundle": self._bundle_to_dict(bundle),
                "version": self._version_to_dict(active_version) if active_version is not None else None,
            }
        version_entry = await repo.activate_version(name, version)
        bundle = await repo.get_bundle(name)
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
            "provider_name": entry.provider_name,
            "model": entry.model,
            "request": entry.request,
            "source_refs": entry.source_refs,
            "prompt_size": entry.prompt_size,
            "created_at": entry.created_at.isoformat(),
        }

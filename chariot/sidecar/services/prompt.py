"""Prompt service for sidecar prompt-query methods."""

from __future__ import annotations

from typing import Any

from chariot.rpc.jsonrpc import JsonRpcServer, RpcError
from chariot.repos.prompt_repo import PromptRepo
from chariot.sidecar.runtime import SidecarRuntime


class PromptService:
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

    @staticmethod
    def _bundle_to_dict(entry) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        return {
            "id": entry.id,
            "name": entry.name,
            "description": entry.description,
            "layers": entry.layers,
            "version_count": entry.version_count,
            "created_at": entry.created_at.isoformat(),
            "updated_at": entry.updated_at.isoformat(),
        }

    @staticmethod
    def _version_to_dict(entry) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        return {
            "id": entry.id,
            "bundle_id": entry.bundle_id,
            "bundle_name": entry.bundle_name,
            "version": entry.version,
            "spec": entry.spec,
            "created_at": entry.created_at.isoformat(),
            "updated_at": entry.updated_at.isoformat(),
        }

    @staticmethod
    def _trace_to_dict(entry) -> dict[str, Any]:  # type: ignore[no-untyped-def]
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

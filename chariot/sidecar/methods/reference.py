"""sidecar reference completion helpers for UI chat composer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chariot.rpc.jsonrpc import JsonRpcServer, RpcContext, RpcError
from chariot.sidecar.methods import MethodBase


class ReferenceMethods(MethodBase):
    """Lightweight completion API for `@reference` UI suggestions."""

    async def complete(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        raw = self._require_str(params, "query")
        if not raw.startswith("@"):
            return {"items": []}
        if raw.startswith("@file:"):
            return {"items": self._complete_file(raw)}
        items: list[dict[str, str]] = []
        for value, kind in (
            ("@file:", "file"),
            ("@url:", "url"),
            ("@diff", "diff"),
            ("@session:", "session"),
        ):
            if value.startswith(raw):
                items.append({"value": value, "label": value, "kind": kind})
        return {"items": items}

    def _complete_file(self, raw: str) -> list[dict[str, str]]:
        prefix = "@file:"
        raw_path = raw[len(prefix) :].replace("\\", "/")
        base_dir, partial = self._split_file_prefix(raw_path)
        cwd = Path.cwd().resolve()
        target_dir = (cwd / base_dir).resolve(strict=False)
        try:
            target_dir.relative_to(cwd)
        except ValueError as e:
            raise RpcError(JsonRpcServer.ERR_INVALID_PARAMS, f"invalid file reference: {raw!r}") from e
        if not target_dir.exists() or not target_dir.is_dir():
            return []
        items: list[dict[str, str]] = []
        for candidate in sorted(target_dir.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            if not candidate.name.startswith(partial):
                continue
            rel = candidate.relative_to(cwd).as_posix()
            if candidate.is_dir():
                rel += "/"
            items.append(
                {
                    "value": f"{prefix}{rel}",
                    "label": rel,
                    "kind": "dir" if candidate.is_dir() else "file",
                }
            )
        return items[:50]

    @staticmethod
    def _split_file_prefix(raw_path: str) -> tuple[Path, str]:
        if not raw_path or raw_path.endswith("/"):
            return Path(raw_path), ""
        base, _, tail = raw_path.rpartition("/")
        return Path(base), tail

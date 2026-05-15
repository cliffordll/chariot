"""Helpers for session-bound domain services."""

from __future__ import annotations

from typing import Any


class SessionRepoProxy:
    """Lazily constructs a repo per async method call using a runtime object."""

    def __init__(self, runtime: Any, repo_cls: type[Any]) -> None:
        self._runtime = runtime
        self._repo_cls = repo_cls
        self._direct_repo = runtime if not hasattr(runtime, "session_maker") else None

    def __getattr__(self, name: str) -> Any:
        async def _call(*args: Any, **kwargs: Any) -> Any:
            if self._direct_repo is not None:
                return await getattr(self._direct_repo, name)(*args, **kwargs)
            async with self._runtime.session_maker() as session:
                repo = self._repo_cls(session)
                return await getattr(repo, name)(*args, **kwargs)

        return _call

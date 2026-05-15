"""Helpers for session-bound domain services."""

from __future__ import annotations

from typing import Any, cast


class SessionRepoProxy:
    """Lazily constructs a repo per async method call using a runtime object."""

    def __init__(self, runtime: Any, repo_cls: type[Any]) -> None:
        self._runtime = runtime
        self._repo_cls = repo_cls

    def __getattr__(self, name: str) -> Any:
        async def _call(*args: Any, **kwargs: Any) -> Any:
            if hasattr(self._runtime, "_sessionmaker") and callable(self._runtime._sessionmaker):
                session_maker = cast(Any, self._runtime._sessionmaker)
                async with session_maker() as session:
                    repo = self._repo_cls(session)
                    return await getattr(repo, name)(*args, **kwargs)
            if callable(self._runtime):
                session_maker = cast(Any, self._runtime)
                async with session_maker() as session:
                    repo = self._repo_cls(session)
                    return await getattr(repo, name)(*args, **kwargs)
            if hasattr(self._runtime, "execute") and hasattr(self._runtime, "commit"):
                repo = self._repo_cls(self._runtime)
                return await getattr(repo, name)(*args, **kwargs)
            return await getattr(self._runtime, name)(*args, **kwargs)

        return _call

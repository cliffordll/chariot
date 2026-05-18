"""Sidecar runtime wrapper for default agent and override-agent cache."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

from chariot.agent.registry import AgentRegistry
from chariot.agent.run import AIAgent

if TYPE_CHECKING:
    from chariot.sidecar.methods import SidecarAgent


class SidecarRuntime:
    """Owns the sidecar default agent and runtime reload/invalidate behavior."""

    _OVERRIDE_SESSION_PREFIX = "sidecar:override:"

    def __init__(
        self,
        agent: SidecarAgent,
        *,
        db_path: Path,
        session_key: str | None = None,
    ) -> None:
        self._agent = agent
        self._db_path = db_path
        self._session_key = session_key

    @property
    def agent(self) -> SidecarAgent:
        return self._agent

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def _sessionmaker(self):  # type: ignore[no-untyped-def]
        return self._agent._sessionmaker

    async def reserve_chat_agent(
        self,
        provider_ref: str | None,
        *,
        base_url: str | None,
        api_key: str | None,
    ) -> SidecarAgent:
        """Return the default agent or a cached override-specific agent."""

        if base_url is None and api_key is None:
            return self._agent

        if provider_ref is None:
            return self._agent

        options: dict[str, str] = {}
        if base_url is not None:
            options["base_url"] = base_url
        if api_key is not None:
            options["api_key"] = api_key

        digest = hashlib.sha256(json.dumps(options, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        session_key = f"{self._OVERRIDE_SESSION_PREFIX}{provider_ref}:{digest}"
        return await AgentRegistry.reserve(
            session_key,
            db_path=self._db_path,
            provider_overrides={provider_ref: options},
        )

    async def reload(self) -> SidecarAgent:
        """Reload the default agent and invalidate override-agent cache."""

        await AgentRegistry.release_prefix(self._OVERRIDE_SESSION_PREFIX)

        if self._session_key is not None:
            await AgentRegistry.release(self._session_key)
            self._agent = await AgentRegistry.reserve(
                self._session_key,
                db_path=self._db_path,
            )
            return self._agent

        self._agent = await AIAgent.bootstrap(self._db_path)
        return self._agent

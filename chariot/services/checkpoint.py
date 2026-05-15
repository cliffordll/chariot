"""Checkpoint domain service."""

from __future__ import annotations

from chariot.repos.checkpoint_repo import CheckpointEntry, CheckpointRepo
from chariot.services._session_proxy import SessionRepoProxy


class CheckpointService:
    def __init__(self, session_maker: object) -> None:
        self._repo = SessionRepoProxy(session_maker, CheckpointRepo)

    async def list_entries(self) -> list[CheckpointEntry]:
        return await self._repo.list_entries()

    async def get_entry(self, checkpoint_id: str) -> CheckpointEntry | None:
        return await self._repo.get_entry(checkpoint_id)

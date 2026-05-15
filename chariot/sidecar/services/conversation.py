"""Sidecar conversation API surface.

对标 `sidecar/services/agent.py`:
- `ConversationApi` 持 `SidecarRuntime`,每个方法里构造 `ConversationService`
- 负责 repo 异常 → RpcError 翻译
- 负责 dataclass → wire dict 序列化
"""

from __future__ import annotations

from typing import Any

from chariot.repos.conversation_repo import ConversationRepo, MessageSearchHit
from chariot.rpc.jsonrpc import JsonRpcServer, RpcError
from chariot.services.conversation import ConversationService
from chariot.sidecar.runtime import SidecarRuntime


class ConversationApi:
    def __init__(self, runtime: SidecarRuntime) -> None:
        self._runtime = runtime

    async def list_conversations(self, session: Any) -> list[dict[str, Any]]:
        service = ConversationService(ConversationRepo(session))
        entries = await service.list_conversations()
        return [self._serialize(c) for c in entries]

    async def get_conversation(self, session: Any, *, conversation_id: str) -> dict[str, Any]:
        service = ConversationService(ConversationRepo(session))
        conversation = await service.get(conversation_id)
        if conversation is None:
            raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"conversation {conversation_id!r} not found")
        messages = await service.get_messages(conversation_id)
        return {"conversation": self._serialize(conversation), "messages": messages}

    async def rename_conversation(
        self,
        session: Any,
        *,
        conversation_id: str,
        title: str | None,
    ) -> dict[str, Any]:
        service = ConversationService(ConversationRepo(session))
        conversation = await service.rename(conversation_id, title)
        return {"conversation": self._serialize(conversation)}

    async def update_config(
        self,
        session: Any,
        *,
        conversation_id: str,
        agent_profile: str | None = None,
    ) -> dict[str, Any]:
        """更新 conversation 的 agent 配置。"""
        service = ConversationService(ConversationRepo(session))
        conversation = await service.update_config(
            conversation_id,
            agent_profile=agent_profile,
        )
        return {"conversation": self._serialize(conversation)}

    async def delete_conversation(self, session: Any, *, conversation_id: str) -> dict[str, Any]:
        service = ConversationService(ConversationRepo(session))
        await service.delete(conversation_id)
        return {"deleted": conversation_id}

    async def search_conversation(
        self,
        session: Any,
        *,
        query: str,
        limit: int = 20,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        service = ConversationService(ConversationRepo(session))
        hits = await service.search(query, limit=limit, conversation_id=conversation_id)
        return {"hits": [self._serialize_hit(h) for h in hits]}

    async def rebuild_conversation_fts(self, session: Any) -> dict[str, Any]:
        service = ConversationService(ConversationRepo(session))
        rebuilt = await service.rebuild_fts()
        return {"rebuilt": rebuilt}

    @staticmethod
    def _serialize_hit(hit: MessageSearchHit) -> dict[str, Any]:
        return {
            "message_id": hit.message_id,
            "conversation_id": hit.conversation_id,
            "role": hit.role,
            "snippet": hit.snippet,
            "rank": hit.rank,
        }

    @staticmethod
    def _serialize(conversation: Any) -> dict[str, Any]:
        """`Conversation` dataclass → wire dict(datetime → ISO str)。"""
        return {
            "id": conversation.id,
            "title": conversation.title,
            "agent_profile": conversation.agent_profile,
            "created_at": conversation.created_at.isoformat(),
            "updated_at": conversation.updated_at.isoformat(),
            "message_count": conversation.message_count,
        }

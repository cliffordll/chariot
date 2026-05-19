"""Conversation domain service。

封装 conversations / messages 的业务操作。
CLI / Sidecar / Gateway 等 surface 层不直接调 Repo,统一走
`ConversationService`,跟 `AgentService` 对齐。

目前 Conversation 业务逻辑较薄,Service 以透传为主;但这里预留扩展点:
- 删除对话时级联清理 trace / audit / checkpoint(未来)
- 创建对话时自动截取首条 user msg 作 title(未来)
- conversation 级别权限检查(未来)
"""

from __future__ import annotations

from typing import Any, Protocol

from chariot.agent.chat_request import Message
from chariot.repos.conversation_repo import Conversation, ConversationRepo
from chariot.services._session_proxy import SessionRepoProxy

__all__ = ["ConversationHistoryStore", "ConversationMessageStore", "ConversationService"]


class ConversationMessageStore(Protocol):
    async def append_assistant_message(
        self,
        conversation_id: str,
        content: list[dict[str, Any]],
        *,
        provider_snapshot: str | None = None,
        agent_profile: str | None = None,
    ) -> Any: ...

    async def append_tool_result_message(
        self,
        conversation_id: str,
        content: list[dict[str, Any]],
    ) -> Any: ...


class ConversationHistoryStore(Protocol):
    async def append_user_message(
        self,
        conversation_id: str,
        content: str | list[dict[str, Any]],
    ) -> Any: ...

    async def load_history_as_messages(self, conversation_id: str) -> list[Message]: ...


class ConversationService:
    """Conversation 领域服务。持有一个 ConversationRepo,提供高层业务方法。"""

    def __init__(self, session_maker: object) -> None:
        self._repo = SessionRepoProxy(session_maker, ConversationRepo)

    # ---- 读 ----

    async def get(self, conversation_id: str) -> Conversation | None:
        return await self._repo.get(conversation_id)

    async def list_conversations(self, limit: int = 50, offset: int = 0) -> list[Conversation]:
        return await self._repo.list_entries(limit=limit, offset=offset)

    async def get_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        """按 Anthropic 协议形态返回 messages 数组。"""
        return await self._repo.load_messages_as_anthropic(conversation_id)

    async def list_message_rows(self, conversation_id: str) -> list[Any]:
        """返回原始 MessageRow 列表(供 CLI show 用)。"""
        return await self._repo.list_messages(conversation_id)

    # ---- 写 ----

    async def create(self, conversation_id: str, *, title: str | None = None) -> Conversation:
        return await self._repo.create(conversation_id, title=title)

    async def ensure_exists(self, conversation_id: str) -> Conversation:
        return await self._repo.ensure_exists(conversation_id)

    async def append_assistant_message(
        self,
        conversation_id: str,
        content: list[dict[str, Any]],
        *,
        provider_snapshot: str | None = None,
        agent_profile: str | None = None,
    ) -> Any:
        return await self._repo.append_message(
            conversation_id,
            role="assistant",
            content=content,
            provider_snapshot=provider_snapshot,
            agent_profile=agent_profile,
        )

    async def append_tool_result_message(
        self,
        conversation_id: str,
        content: list[dict[str, Any]],
    ) -> Any:
        return await self._repo.append_message(
            conversation_id,
            role="user",
            content=content,
        )

    async def append_user_message(
        self,
        conversation_id: str,
        content: str | list[dict[str, Any]],
    ) -> Any:
        return await self._repo.append_message(
            conversation_id,
            role="user",
            content=content,
        )

    async def load_history_as_messages(self, conversation_id: str) -> list[Message]:
        rows = await self._repo.load_messages_as_anthropic(conversation_id)
        return [Message(role=row["role"], content=row["content"]) for row in rows]

    async def delete(self, conversation_id: str) -> None:
        return await self._repo.delete(conversation_id)

    async def rename(self, conversation_id: str, title: str | None) -> Conversation:
        return await self._repo.update_title(conversation_id, title=title)

    async def update_config(
        self,
        conversation_id: str,
        *,
        agent_profile: str | None = None,
    ) -> Conversation:
        """更新 conversation 的 agent 配置。"""
        return await self._repo.update_config(
            conversation_id,
            agent_profile=agent_profile,
        )

    # ---- C1: 配置恢复 ----

    async def get_config(self, conversation_id: str) -> tuple[str | None, str | None]:
        """获取对话持久化的 agent 配置。

        返回 (None, agent_profile);找不到对话 → (None, None)。
        provider 由 agent_profile 绑定自动推导,不再单独存储。
        """
        conv = await self._repo.get(conversation_id)
        if conv is None:
            return None, None
        return None, conv.agent_profile

    # ---- 搜索 ----

    async def search(
        self,
        query: str,
        *,
        limit: int = 20,
        conversation_id: str | None = None,
    ) -> list[Any]:
        return await self._repo.search(query, limit=limit, conversation_id=conversation_id)

    async def rebuild_fts(self) -> int:
        return await self._repo.rebuild_fts()

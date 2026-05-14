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

from typing import Any

from chariot.repos.conversation_repo import Conversation, ConversationRepo

__all__ = ["ConversationService"]


class ConversationService:
    """Conversation 领域服务。持有一个 ConversationRepo,提供高层业务方法。"""

    def __init__(self, repo: ConversationRepo) -> None:
        self._repo = repo

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

    async def delete(self, conversation_id: str) -> None:
        return await self._repo.delete(conversation_id)

    async def rename(self, conversation_id: str, title: str | None) -> Conversation:
        return await self._repo.update_title(conversation_id, title=title)

    # ---- C1: 配置恢复 ----

    async def get_config(self, conversation_id: str) -> tuple[str | None, str | None]:
        """获取对话持久化的 provider + agent 配置。

        返回 (last_provider, agent_profile);找不到对话 → (None, None)。
        """
        conv = await self._repo.get(conversation_id)
        if conv is None:
            return None, None
        return conv.last_provider, conv.agent_profile

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

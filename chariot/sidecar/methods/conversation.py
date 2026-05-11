"""sidecar conversation method handlers(0.6.5 S.8c)。

`ConversationMethods`:list / get / rename / delete 4 个 method,全部走
`MethodBase._session()` 开 DB session + 翻译 repo 异常 → RpcError。
"""

from __future__ import annotations

from typing import Any

from chariot.repos.conversation_repo import Conversation, ConversationRepo
from chariot.rpc.jsonrpc import JsonRpcServer, RpcContext, RpcError
from chariot.sidecar.methods import MethodBase


class ConversationMethods(MethodBase):
    """conversation CRUD method handlers(list_conversations / get_conversation / rename_conversation / delete_conversation)。"""

    async def list_(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """`list_conversations`:按 created_at 降序列所有 conversation(简略字段)。

        params:无;返:`{"conversations": [{id, title, last_model, created_at,
        updated_at, message_count}, ...]}`
        """
        del params, ctx
        async with self._session() as session:
            entries = await ConversationRepo(session).list_entries()
        return {"conversations": [self._serialize(c) for c in entries]}

    async def get(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """`get_conversation`:按 id 取 conversation + 全部 messages(Anthropic 形态)。

        params:`{"conversation_id": str}`;返:`{conversation: {...}, messages: [{role,
        content}, ...]}`(messages 已是 Anthropic 协议形态,直接喂前端渲染)
        """
        del ctx
        conversation_id = self._require_str(params, "conversation_id")
        async with self._session() as session:
            repo = ConversationRepo(session)
            conversation = await repo.get(conversation_id)
            if conversation is None:
                raise RpcError(
                    JsonRpcServer.ERR_NOT_FOUND, f"conversation {conversation_id!r} not found"
                )
            messages = await repo.load_messages_as_anthropic(conversation_id)
        return {"conversation": self._serialize(conversation), "messages": messages}

    async def rename(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """`rename_conversation`:改 conversation title。title=None 清空。"""
        del ctx
        conversation_id = self._require_str(params, "conversation_id")
        title = self._optional_str(params, "title")
        async with self._session() as session:
            conversation = await ConversationRepo(session).update_title(conversation_id, title)
        return {"conversation": self._serialize(conversation)}

    async def delete(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """`delete_conversation`:删 conversation + cascade 删 messages。"""
        del ctx
        conversation_id = self._require_str(params, "conversation_id")
        async with self._session() as session:
            await ConversationRepo(session).delete(conversation_id)
        return {"deleted": conversation_id}

    @staticmethod
    def _serialize(conversation: Conversation) -> dict[str, Any]:
        """`Conversation` dataclass → wire dict(datetime → ISO str)。"""
        return {
            "id": conversation.id,
            "title": conversation.title,
            "last_model": conversation.last_model,
            "created_at": conversation.created_at.isoformat(),
            "updated_at": conversation.updated_at.isoformat(),
            "message_count": conversation.message_count,
        }

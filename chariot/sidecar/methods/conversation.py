"""sidecar conversation method handlers(0.6.5 S.8c)。

`ConversationMethods`:list / get / rename / delete 4 个 method,全部走
`ConversationApi` → `ConversationService` → `ConversationRepo`。
本层只负责:JSON-RPC 参数解析 + 调用 Api + 异常翻译。
"""

from __future__ import annotations

from typing import Any

from chariot.rpc.jsonrpc import RpcContext
from chariot.sidecar.methods import MethodBase
from chariot.sidecar.services.conversation import ConversationApi


class ConversationMethods(MethodBase):
    """conversation CRUD method handlers(list_conversations / get_conversation / rename_conversation / delete_conversation)。"""  # noqa: E501

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._api = ConversationApi(self.runtime)

    async def list_(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """`list_conversations`:按 created_at 降序列所有 conversation(简略字段)。

        params:无;返:`{"conversations": [{id, title, last_provider, created_at,
        updated_at, message_count}, ...]}`
        """
        del params, ctx
        async with self._session() as session:
            entries = await self._api.list_conversations(session)
        return {"conversations": entries}

    async def get(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """`get_conversation`:按 id 取 conversation + 全部 messages(Anthropic 形态)。

        params:`{"conversation_id": str}`;返:`{conversation: {...}, messages: [{role,
        content}, ...]}`(messages 已是 Anthropic 协议形态,直接喂前端渲染)
        """
        del ctx
        conversation_id = self._require_str(params, "conversation_id")
        async with self._session() as session:
            return await self._api.get_conversation(session, conversation_id=conversation_id)

    async def rename(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """`rename_conversation`:改 conversation title。title=None 清空。"""
        del ctx
        conversation_id = self._require_str(params, "conversation_id")
        title = self._optional_str(params, "title")
        async with self._session() as session:
            return await self._api.rename_conversation(session, conversation_id=conversation_id, title=title)

    async def delete(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """`delete_conversation`:删 conversation + cascade 删 messages。"""
        del ctx
        conversation_id = self._require_str(params, "conversation_id")
        async with self._session() as session:
            return await self._api.delete_conversation(session, conversation_id=conversation_id)

    async def search(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """`search_conversation`(B3 wave 1):FTS5 全文搜索 messages。

        params:`{"query": str, "limit"?: int, "conversation_id"?: str}`
        返:`{"hits": [{message_id, conversation_id, role, snippet, rank}, ...]}`
        """
        del ctx
        query = self._require_str(params, "query")
        limit_raw = params.get("limit")
        limit = int(limit_raw) if isinstance(limit_raw, int) and not isinstance(limit_raw, bool) else 20
        conversation_id = self._optional_str(params, "conversation_id")
        async with self._session() as session:
            return await self._api.search_conversation(
                session,
                query=query,
                limit=limit,
                conversation_id=conversation_id,
            )

    async def rebuild_fts(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """`rebuild_conversation_fts`(B3 wave 1):灾备清空 + 全量回填 messages_fts。"""
        del params, ctx
        async with self._session() as session:
            return await self._api.rebuild_conversation_fts(session)

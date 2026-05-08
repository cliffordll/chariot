"""sidecar convo method handlers(0.6.5 S.8c)。

`ConvoMethods`:list / get / rename / delete 4 个 method,全部走
`MethodBase._session()` 开 DB session + 翻译 repo 异常 → RpcError。
"""

from __future__ import annotations

from typing import Any

from chariot.repos.convo_repo import Convo, ConvoRepo
from chariot.rpc.jsonrpc import JsonRpcServer, RpcContext, RpcError
from chariot.sidecar.methods import MethodBase


class ConvoMethods(MethodBase):
    """convo CRUD method handlers(list_convos / get_convo / rename_convo / delete_convo)。"""

    async def list_(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """`list_convos`:按 created_at 降序列所有 convo(简略字段)。

        params:无;返:`{"convos": [{id, title, last_model, created_at,
        updated_at, message_count}, ...]}`
        """
        del params, ctx
        async with self._session() as session:
            entries = await ConvoRepo(session).list_entries()
        return {"convos": [self._serialize(c) for c in entries]}

    async def get(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """`get_convo`:按 id 取 convo + 全部 messages(Anthropic 形态)。

        params:`{"convo_id": str}`;返:`{convo: {...}, messages: [{role,
        content}, ...]}`(messages 已是 Anthropic 协议形态,直接喂前端渲染)
        """
        del ctx
        convo_id = self._require_str(params, "convo_id")
        async with self._session() as session:
            repo = ConvoRepo(session)
            convo = await repo.get(convo_id)
            if convo is None:
                raise RpcError(JsonRpcServer.ERR_NOT_FOUND, f"convo {convo_id!r} not found")
            messages = await repo.load_messages_as_anthropic(convo_id)
        return {"convo": self._serialize(convo), "messages": messages}

    async def rename(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """`rename_convo`:改 convo title。title=None 清空。"""
        del ctx
        convo_id = self._require_str(params, "convo_id")
        title = self._optional_str(params, "title")
        async with self._session() as session:
            convo = await ConvoRepo(session).update_title(convo_id, title)
        return {"convo": self._serialize(convo)}

    async def delete(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """`delete_convo`:删 convo + cascade 删 messages。"""
        del ctx
        convo_id = self._require_str(params, "convo_id")
        async with self._session() as session:
            await ConvoRepo(session).delete(convo_id)
        return {"deleted": convo_id}

    @staticmethod
    def _serialize(convo: Convo) -> dict[str, Any]:
        """`Convo` dataclass → wire dict(datetime → ISO str)。"""
        return {
            "id": convo.id,
            "title": convo.title,
            "last_model": convo.last_model,
            "created_at": convo.created_at.isoformat(),
            "updated_at": convo.updated_at.isoformat(),
            "message_count": convo.message_count,
        }

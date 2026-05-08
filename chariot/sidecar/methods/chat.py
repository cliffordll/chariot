"""sidecar `chat` method handler(0.6.5 S.8b)。

`ChatMethod`:实例承载 `agent` 引用;`__call__` 暴露 RPC handler 签名。
跑一次 chat → 流式 notify ChatEvent + 终止 response。

`_RequestDecoder`:RPC params dict → typed `ChatRequest`,字段错抛 `RpcError`。
RPC 反序列化是 sidecar 特有的事(ChatRequest 自身不该耦合 RPC 错误码),
所以解码逻辑住这。文件内私有(下划线前缀),只有 `ChatMethod` 调用。
"""

from __future__ import annotations

import dataclasses
import time
import uuid
from typing import Any, cast

from chariot.agent.chat_request import ChatRequest, Message
from chariot.rpc.jsonrpc import JsonRpcServer, RpcContext, RpcError
from chariot.sidecar.methods import MethodBase


class ChatMethod(MethodBase):
    """sidecar `chat` method handler。`__call__` 暴露 handler 签名。"""

    async def __call__(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """跑一次 chat,流式 notify ChatEvent + 终止 response。

        流程:
        1. params dict → ChatRequest(_RequestDecoder.parse)
        2. agent.run(req) 流式产 ChatEvent
        3. 每个 event 走 ctx.notify("chat_event", asdict(event))
        4. 流结束返 {stream_id, ended_at}

        params 缺关键字段 / 类型错 → 抛 RpcError(ERR_INVALID_PARAMS)。
        """
        req = _RequestDecoder.parse(params)
        stream_id = uuid.uuid4().hex
        async for event in self.agent.run(req):
            await ctx.notify("chat_event", dataclasses.asdict(event))
        return {
            "stream_id": stream_id,
            "ended_at": time.time(),  # epoch 浮点秒;前端按需转 ISO
        }


class _RequestDecoder:
    """RPC params dict → typed `ChatRequest`;字段错抛 `RpcError`。

    全 classmethod / staticmethod 形态(无实例状态),纯 namespace 类。
    只校验"必填字段缺 / 顶层类型错"这种粗粒度;细粒度字段语义错由
    ChatRequest 构造或下游 Provider 报。
    """

    # ChatRequest 里的可选字段(顺序跟 chat_request.py 字段顺序一致)
    _OPTIONAL_FIELDS = (
        "model",
        "max_tokens",
        "system",
        "tools",
        "tool_choice",
        "temperature",
        "top_p",
        "top_k",
        "stop_sequences",
        "metadata",
        "thinking",
        "convo_id",
        "agent_id",
    )

    @classmethod
    def parse(cls, params: dict[str, Any]) -> ChatRequest:
        """params dict → ChatRequest;字段错抛 RpcError(ERR_INVALID_PARAMS)。"""
        pn = params.get("provider_name")
        if not isinstance(pn, str) or not pn:
            raise RpcError(
                JsonRpcServer.ERR_INVALID_PARAMS,
                "'provider_name' is required and must be a non-empty string",
            )

        msgs_raw = params.get("messages")
        if not isinstance(msgs_raw, list) or not msgs_raw:
            raise RpcError(
                JsonRpcServer.ERR_INVALID_PARAMS,
                "'messages' is required and must be a non-empty list",
            )
        msgs_list = cast(list[Any], msgs_raw)

        messages = [cls._message(m, idx=i) for i, m in enumerate(msgs_list)]

        kwargs: dict[str, Any] = {"provider_name": pn, "messages": messages}
        for key in cls._OPTIONAL_FIELDS:
            if key in params:
                kwargs[key] = params[key]

        try:
            return ChatRequest(**kwargs)
        except (TypeError, ValueError) as e:
            raise RpcError(
                JsonRpcServer.ERR_INVALID_PARAMS,
                f"invalid chat request: {e}",
            ) from e

    @staticmethod
    def _message(raw: object, *, idx: int) -> Message:
        """单条 message dict → Message;格式错抛 RpcError。"""
        if not isinstance(raw, dict):
            raise RpcError(
                JsonRpcServer.ERR_INVALID_PARAMS,
                f"messages[{idx}] must be an object",
            )
        m = cast(dict[str, Any], raw)
        role_raw = m.get("role")
        content_raw = m.get("content")
        if role_raw not in ("user", "assistant"):
            raise RpcError(
                JsonRpcServer.ERR_INVALID_PARAMS,
                f"messages[{idx}].role must be 'user' or 'assistant', got {role_raw!r}",
            )
        if not isinstance(content_raw, str | list):
            raise RpcError(
                JsonRpcServer.ERR_INVALID_PARAMS,
                f"messages[{idx}].content must be a string or list of blocks",
            )
        return Message(
            role=role_raw,
            content=content_raw
            if isinstance(content_raw, str)
            else cast("list[dict[str, Any]]", content_raw),
        )

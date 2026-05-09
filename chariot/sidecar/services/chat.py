"""Chat decoder and service for the sidecar adapter layer."""

from __future__ import annotations

import uuid
from typing import Any, cast

from chariot.agent.chat_request import ChatRequest, Message
from chariot.rpc.jsonrpc import JsonRpcServer, RpcError
from chariot.sidecar.runtime import SidecarRuntime


class ChatService:
    def __init__(self, runtime: SidecarRuntime) -> None:
        self._runtime = runtime

    async def open_chat(self, params: dict[str, Any]) -> tuple[str, ChatRequest, Any]:
        req = ChatRequestDecoder.parse(params)
        base_url = self._optional_str(params, "base_url")
        api_key = self._optional_str(params, "api_key")
        agent = await self._runtime.reserve_chat_agent(
            req.provider_name,
            base_url=base_url,
            api_key=api_key,
        )
        return uuid.uuid4().hex, req, agent.run_chat(req)

    @staticmethod
    def _optional_str(params: dict[str, Any], key: str) -> str | None:
        val = params.get(key)
        if val is None:
            return None
        if not isinstance(val, str):
            raise RpcError(
                JsonRpcServer.ERR_INVALID_PARAMS,
                f"{key!r} must be a string or null",
            )
        return val


class ChatRequestDecoder:
    """RPC params dict -> typed ``ChatRequest``."""

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
        "conversation_id",
        "agent_id",
    )

    @classmethod
    def parse(cls, params: dict[str, Any]) -> ChatRequest:
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

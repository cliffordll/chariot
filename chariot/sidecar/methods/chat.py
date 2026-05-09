"""sidecar `chat` RPC adapter."""

from __future__ import annotations

import dataclasses
import time
from typing import Any

from chariot.rpc.jsonrpc import RpcContext
from chariot.sidecar.methods import MethodBase
from chariot.sidecar.runtime import SidecarRuntime
from chariot.sidecar.services import ChatService


class ChatMethod(MethodBase):
    """Thin RPC adapter: decoder/service -> JSON-RPC notify/response."""

    def __init__(self, runtime: SidecarRuntime) -> None:
        super().__init__(runtime)
        self._service = ChatService(runtime)

    async def __call__(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        stream_id, _req, event_stream = await self._service.open_chat(params)
        async for event in event_stream:
            await ctx.notify("chat_event", dataclasses.asdict(event))
        return {
            "stream_id": stream_id,
            "ended_at": time.time(),
        }

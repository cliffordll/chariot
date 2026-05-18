"""sidecar `chat` RPC adapter."""

from __future__ import annotations

import contextlib
import dataclasses
import time
from typing import Any

from chariot.rpc.jsonrpc import RpcContext
from chariot.sidecar.methods import MethodBase
from chariot.sidecar.runtime import SidecarRuntime
from chariot.sidecar.services import ChatService, LogApi


class ChatMethod(MethodBase):
    """Thin RPC adapter: decoder/service -> JSON-RPC notify/response."""

    def __init__(self, runtime: SidecarRuntime) -> None:
        super().__init__(runtime)
        self._service = ChatService(runtime)

    async def __call__(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        stream_id, req, event_stream = await self._service.open_chat(params)
        started_at = time.perf_counter()
        status = "ok"
        error_message: str | None = None
        input_tokens: int | None = None
        output_tokens: int | None = None
        try:
            async for event in event_stream:
                usage = event.usage or (event.message.get("usage") if event.message else None)
                if isinstance(usage, dict):
                    if isinstance(usage.get("input_tokens"), int):
                        input_tokens = usage["input_tokens"]
                    if isinstance(usage.get("output_tokens"), int):
                        output_tokens = usage["output_tokens"]
                if event.kind == "error":
                    status = "error"
                    error_message = event.error_message or event.error_type or "chat error"
                await ctx.notify(
                    "chat_event",
                    {
                        "stream_id": stream_id,
                        **dataclasses.asdict(event),
                    },
                )
        except Exception:
            await self._write_log(
                provider=req.provider_ref,
                status="error",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                started_at=started_at,
                error=error_message,
            )
            raise
        await self._write_log(
            provider=req.provider_ref,
            status=status,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            started_at=started_at,
            error=error_message,
        )
        return {"stream_id": stream_id, "ended_at": time.time()}

    async def _write_log(
        self,
        *,
        provider: str | None,
        status: str,
        input_tokens: int | None,
        output_tokens: int | None,
        started_at: float,
        error: str | None,
    ) -> None:
        with contextlib.suppress(Exception):
            await LogApi(self.runtime).create(
                provider=provider,
                status=status,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=int((time.perf_counter() - started_at) * 1000),
                error=error,
            )

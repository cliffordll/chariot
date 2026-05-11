"""`@url:<full-url>` resolver(B3 wave 3)。

Security 红线:
- 仅 http(s) scheme
- host 必须在 `ctx.allowed_domains` 白名单内(继承 `http_get` 工具的同套白名单)
- timeout 10s
- body 截到 `ctx.max_bytes`(utf-8 decode 失败 → base64)
"""

from __future__ import annotations

import base64
from typing import ClassVar
from urllib.parse import urlparse

import httpx

from chariot.context.references.base import (
    BaseReferenceResolver,
    ReferenceResolveError,
    ResolveContext,
    ResolvedReference,
)

_DEFAULT_TIMEOUT_S = 10.0


class UrlReferenceResolver(BaseReferenceResolver):
    type_id: ClassVar[str] = "url"

    async def resolve(self, key: str, ctx: ResolveContext) -> ResolvedReference:
        try:
            parsed = urlparse(key)
        except ValueError as e:
            raise ReferenceResolveError("invalid_url", type_id=self.type_id, key=key, detail=str(e)) from e
        if parsed.scheme not in ("http", "https"):
            raise ReferenceResolveError(
                "invalid_scheme",
                type_id=self.type_id,
                key=key,
                detail=f"scheme={parsed.scheme!r}",
            )
        host = (parsed.hostname or "").lower()
        if not host:
            raise ReferenceResolveError("missing_host", type_id=self.type_id, key=key)
        if host not in ctx.allowed_domains:
            raise ReferenceResolveError(
                "disallowed_host",
                type_id=self.type_id,
                key=key,
                detail=f"{host!r} not in allowlist",
            )
        try:
            async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_S) as client:
                resp = await client.get(key)
        except httpx.TimeoutException as e:
            raise ReferenceResolveError("timeout", type_id=self.type_id, key=key) from e
        except httpx.RequestError as e:
            raise ReferenceResolveError("request_error", type_id=self.type_id, key=key, detail=str(e)) from e

        raw = resp.content[: ctx.max_bytes]
        truncated = len(resp.content) > ctx.max_bytes
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = f"[base64] {base64.b64encode(raw).decode('ascii')}"
        body = f"HTTP {resp.status_code}\n\n{text}"
        return ResolvedReference(type_id=self.type_id, key=key, content=body, truncated=truncated)

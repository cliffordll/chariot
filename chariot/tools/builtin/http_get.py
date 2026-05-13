"""HTTP GET tool with allow-list and max-bytes guardrails."""

from __future__ import annotations

import base64
import json
from typing import Any, ClassVar, Self, cast
from urllib.parse import urlparse

import httpx

from chariot.agent.exceptions import ConfigError
from chariot.models.tool import ToolEntry
from chariot.tools.base import BaseTool
from chariot.tools.builtin._meta import builtin_tool

_DEFAULT_MAX_BYTES = 524288
_REQUEST_TIMEOUT_S = 30.0


@builtin_tool(defaults={"allowed_domains": [], "max_bytes": _DEFAULT_MAX_BYTES})
class HttpGetTool(BaseTool):
    _DESCRIPTION: ClassVar[str] = (
        "Make an HTTP GET request to a URL on the configured allow-list. "
        "Returns response status, headers, and body (truncated to max_bytes). "
        "URLs not on the allow-list are rejected."
    )

    def __init__(
        self,
        name: str,
        allowed_domains: tuple[str, ...],
        max_bytes: int,
    ) -> None:
        self.name = name
        self.allowed_domains = allowed_domains
        self.max_bytes = max_bytes

    @classmethod
    def create(cls, entry: ToolEntry) -> Self:
        allowed_raw = entry.options.get("allowed_domains", [])
        max_bytes = entry.options.get("max_bytes", _DEFAULT_MAX_BYTES)
        allowed_list = cls._parse_allowed_domains(allowed_raw)
        if not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ConfigError(f"http_get.options.max_bytes 必须是正整数,得到 {max_bytes!r}")
        return cls(
            name=entry.name,
            allowed_domains=tuple(d.lower() for d in allowed_list),
            max_bytes=max_bytes,
        )

    @staticmethod
    def _parse_allowed_domains(raw: Any) -> list[str]:
        """Accept canonical list[str] plus a few legacy string encodings."""

        if isinstance(raw, list) and all(isinstance(d, str) for d in cast(list[Any], raw)):
            return cast(list[str], raw)

        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return []
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list) and all(isinstance(d, str) for d in cast(list[Any], parsed)):
                return cast(list[str], parsed)
            if text.startswith("[") and text.endswith("]"):
                inner = text[1:-1].strip()
                if not inner:
                    return []
                return [part.strip().strip("'\"") for part in inner.split(",") if part.strip()]
            return [part.strip() for part in text.split(",") if part.strip()]

        raise ConfigError(f"http_get.options.allowed_domains 必须是字符串数组,得到 {raw!r}")

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self._DESCRIPTION,
            "input_schema": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Full URL (http or https).",
                    },
                    "headers": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "description": "Extra request headers.",
                    },
                },
                "required": ["url"],
            },
        }

    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        url_raw = input.get("url")
        headers_raw = input.get("headers")
        if not isinstance(url_raw, str) or not url_raw:
            return self._error("input.url 必须是非空字符串")
        if headers_raw is not None and not isinstance(headers_raw, dict):
            return self._error("input.headers 必须是字符串字典")
        headers: dict[str, str] = {}
        if isinstance(headers_raw, dict):
            for k, v in cast(dict[Any, Any], headers_raw).items():
                if not isinstance(k, str) or not isinstance(v, str):
                    return self._error("input.headers 的 key/value 必须是字符串")
                headers[k] = v

        try:
            parsed = urlparse(url_raw)
        except ValueError as e:
            return self._error(f"URL 解析失败: {e}")
        if parsed.scheme not in ("http", "https"):
            return self._error(f"仅支持 http(s),得到 scheme={parsed.scheme!r}")
        host = (parsed.hostname or "").lower()
        if not host:
            return self._error("URL 缺少 host")
        if host not in self.allowed_domains:
            return self._error(f"host {host!r} 不在白名单(白名单大小={len(self.allowed_domains)})")

        try:
            async with self._make_client() as client:
                resp = await client.get(url_raw, headers=headers)
        except httpx.RequestError as e:
            return self._error(f"GET 失败: {e}")

        raw = resp.content
        truncated = len(raw) > self.max_bytes
        clipped = raw[: self.max_bytes]
        try:
            body = clipped.decode("utf-8")
        except UnicodeDecodeError:
            body = f"[base64] {base64.b64encode(clipped).decode('ascii')}"

        headers_out: dict[str, str] = {str(k).lower(): str(v) for k, v in resp.headers.items()}
        result = {
            "status": resp.status_code,
            "headers": headers_out,
            "body": body,
            "truncated": truncated,
        }
        return {
            "type": "tool_result",
            "content": [
                {"type": "text", "text": json.dumps(result, ensure_ascii=False)},
            ],
        }

    def _make_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S)

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {
            "type": "tool_result",
            "content": [{"type": "text", "text": message}],
            "is_error": True,
        }

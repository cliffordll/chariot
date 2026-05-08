"""HttpGetTool — HTTP GET,带域白名单 + max_bytes 限制(0.4.0 内置工具)。

options
-------
- `allowed_domains` (list[str]):允许访问的 host 精确匹配列表。
  **空列表 → 所有 GET 拒绝**(等价工具禁用)
- `max_bytes` (int):响应体最大字节数,默认 524288(512 KiB)

输入 schema
-----------
- `url` (str, required):完整 URL(必须 http/https)
- `headers` (dict[str, str], optional):额外请求头

返回
----
- 成功:`tool_result` 含 `{status, body, headers, truncated}` 的 JSON
  - body 是字符串(UTF-8 解码;失败 fallback base64,前缀 `[base64]`)
  - headers 是响应头 dict(全小写 key)
  - truncated 表示是否被 max_bytes 截断
- 失败:is_error=True(域不在白名单 / URL 非 http(s) / 网络错误)

安全注意
--------
- 域白名单严格精确匹配 host,**不支持子域通配**(`*.example.com`)
- 不支持其它 method(POST / PUT 等);0.4.x 看需求
- 不跟随重定向到 _未_ 在白名单的域(httpx 默认不跟随 redirect,这里也保持)

模块级零自由函数,所有逻辑收在 `HttpGetTool` 类里。
"""

from __future__ import annotations

import base64
import json
from typing import Any, ClassVar, Self, cast
from urllib.parse import urlparse

import httpx

from chariot.agent.config import ConfigError, ToolEntry
from chariot.tools.base import BaseTool

_DEFAULT_MAX_BYTES = 524288  # 512 KiB
_REQUEST_TIMEOUT_S = 30.0


class HttpGetTool(BaseTool):
    """HTTP GET 工具,带域白名单 + 响应体大小限制。"""

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
        if not isinstance(allowed_raw, list) or not all(
            isinstance(d, str) for d in cast(list[Any], allowed_raw)
        ):
            raise ConfigError(
                f"http_get.options.allowed_domains 必须是字符串数组,得到 {allowed_raw!r}"
            )
        if not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ConfigError(f"http_get.options.max_bytes 必须是正整数,得到 {max_bytes!r}")
        allowed_list = cast(list[str], allowed_raw)
        return cls(
            name=entry.name,
            allowed_domains=tuple(d.lower() for d in allowed_list),
            max_bytes=max_bytes,
        )

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
        """生成发请求用的 client。测试侧可在实例上替换这个方法注入 MockTransport。"""
        return httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S)

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {
            "type": "tool_result",
            "content": [{"type": "text", "text": message}],
            "is_error": True,
        }

"""WebExtractTool — 网页内容提取工具(0.8.7)。

URL → 可读 markdown。支持 allowed_domains 限制。

options
-------
- `max_bytes` (int):最大提取字节数,默认 100KB
- `allowed_domains` (list[str] | None):允许提取的域名列表,None = 不限
"""

from __future__ import annotations

from typing import Any, ClassVar, Self
from urllib.parse import urlparse

import httpx

from chariot.agent.exceptions import ConfigError
from chariot.models.tool import ToolEntry
from chariot.tools.base import BaseTool

_DEFAULT_MAX_BYTES = 102400  # 100KB
_REQUEST_TIMEOUT_S = 30.0


class WebExtractTool(BaseTool):
    """网页内容提取。URL → 可读 markdown。"""

    _DESCRIPTION: ClassVar[str] = (
        "Extract readable content from a web page and return it as markdown. Uses HTTP GET + HTML-to-text extraction."
    )

    def __init__(
        self,
        name: str,
        max_bytes: int,
        allowed_domains: tuple[str, ...] | None,
    ) -> None:
        self.name = name
        self.max_bytes = max_bytes
        self.allowed_domains = allowed_domains

    @classmethod
    def create(cls, entry: ToolEntry) -> Self:
        max_bytes = entry.options.get("max_bytes", _DEFAULT_MAX_BYTES)
        allowed_raw = entry.options.get("allowed_domains")
        if not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ConfigError(f"web_extract.options.max_bytes 必须是正整数,得到 {max_bytes!r}")
        allowed = None
        if allowed_raw is not None:
            if not isinstance(allowed_raw, list) or not all(isinstance(d, str) for d in allowed_raw):
                raise ConfigError("web_extract.options.allowed_domains 必须是字符串列表或 null")
            allowed = tuple(d.lower() for d in allowed_raw)
        return cls(name=entry.name, max_bytes=max_bytes, allowed_domains=allowed)

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self._DESCRIPTION,
            "input_schema": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to extract content from.",
                    },
                },
                "required": ["url"],
            },
        }

    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        url = input.get("url")
        if not isinstance(url, str) or not url:
            return self._error("input.url 必须是非空字符串")

        # 域名检查
        parsed = urlparse(url)
        domain = (parsed.netloc or "").lower()
        if not domain:
            return self._error(f"URL 无效,无法解析域名: {url}")
        if self.allowed_domains is not None and domain not in self.allowed_domains:
            return self._error(f"域名 {domain!r} 不在 allowed_domains 列表中;如需访问,请在工具配置中添加该域名")

        # 拉取 HTML
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S, follow_redirects=True) as client:
                response = await client.get(url, headers={"User-Agent": "Chariot-Agent/0.8.7"})
                response.raise_for_status()
                html = response.text
        except httpx.HTTPStatusError as e:
            return self._error(f"HTTP 错误: {e.response.status_code} {url}")
        except httpx.RequestError as e:
            return self._error(f"请求失败: {e}")
        except Exception as e:
            return self._error(f"拉取页面失败: {e}")

        # 提取正文
        text = self._extract_text(html, url)
        if len(text) > self.max_bytes:
            text = text[: self.max_bytes] + "\n\n... [truncated]"

        return {
            "type": "tool_result",
            "content": [
                {
                    "type": "text",
                    "text": f"# {url}\n\n{text}",
                }
            ],
        }

    def _extract_text(self, html: str, url: str) -> str:
        """从 HTML 提取正文。优先用 trafilatura, fallback 到简单清理。"""
        try:
            import trafilatura

            extracted = trafilatura.extract(html, url=url, include_comments=False, include_tables=False)
            if extracted:
                return extracted
        except ImportError:
            pass
        except Exception:
            pass

        # fallback: 简单清理
        import re

        # 移除 script/style 标签及内容
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        # 移除 HTML 标签
        text = re.sub(r"<[^>]+>", "", text)
        # 合并空白
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {
            "type": "tool_result",
            "content": [{"type": "text", "text": message}],
            "is_error": True,
        }

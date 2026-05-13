"""WebSearchTool — 网页搜索工具(0.8.7)。

支持 DuckDuckGo(免费,无需 API key)和 Tavily(需要 API key)两种后端。

options
-------
- `backend` (str):"duckduckgo" | "tavily",默认 "duckduckgo"
- `max_results` (int):默认 5
- `include_answer` (bool):Tavily 专用,是否返直接答案,默认 True
"""

from __future__ import annotations

import os
from typing import Any, ClassVar, Self

from chariot.agent.exceptions import ConfigError
from chariot.models.tool import ToolEntry
from chariot.tools.base import BaseTool
from chariot.tools.builtin._meta import builtin_tool


@builtin_tool(defaults={"backend": "duckduckgo", "max_results": 5, "include_answer": True})
class WebSearchTool(BaseTool):
    """网页搜索工具。支持 DuckDuckGo 和 Tavily。"""

    _DESCRIPTION: ClassVar[str] = (
        "Search the web for information. "
        "DuckDuckGo backend works without API key; "
        "Tavily backend requires TAVILY_API_KEY env var."
    )

    def __init__(
        self,
        name: str,
        backend: str,
        max_results: int,
        include_answer: bool,
    ) -> None:
        self.name = name
        self.backend = backend
        self.max_results = max_results
        self.include_answer = include_answer

    @classmethod
    def create(cls, entry: ToolEntry) -> Self:
        backend = entry.options.get("backend", "duckduckgo")
        max_results = entry.options.get("max_results", 5)
        include_answer = entry.options.get("include_answer", True)
        if backend not in ("duckduckgo", "tavily"):
            raise ConfigError(f"web_search.options.backend 必须是 'duckduckgo' 或 'tavily',得到 {backend!r}")
        if not isinstance(max_results, int) or max_results <= 0:
            raise ConfigError(f"web_search.options.max_results 必须是正整数,得到 {max_results!r}")
        if not isinstance(include_answer, bool):
            raise ConfigError(f"web_search.options.include_answer 必须是 bool,得到 {include_answer!r}")
        return cls(
            name=entry.name,
            backend=backend,
            max_results=max_results,
            include_answer=include_answer,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self._DESCRIPTION,
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query text.",
                    },
                },
                "required": ["query"],
            },
        }

    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        query = input.get("query")
        if not isinstance(query, str) or not query:
            return self._error("input.query 必须是非空字符串")

        if self.backend == "duckduckgo":
            return await self._search_duckduckgo(query)
        elif self.backend == "tavily":
            return await self._search_tavily(query)
        else:
            return self._error(f"未知 backend: {self.backend}")

    async def _search_duckduckgo(self, query: str) -> dict[str, Any]:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            return self._error("DuckDuckGo 搜索需要 duckduckgo-search 库;请运行: uv add duckduckgo-search")

        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=self.max_results))
        except Exception as e:
            return self._error(f"DuckDuckGo 搜索失败: {e}")

        if not results:
            return {
                "type": "tool_result",
                "content": [{"type": "text", "text": f"未找到结果: {query}"}],
            }

        lines = [f"🔍 {query}", ""]
        for i, r in enumerate(results, 1):
            title = r.get("title", "(无标题)")
            href = r.get("href", "")
            snippet = r.get("body", "")
            lines.append(f"{i}. {title}")
            lines.append(f"   {href}")
            if snippet:
                lines.append(f"   {snippet[:200]}")
            lines.append("")

        return {
            "type": "tool_result",
            "content": [{"type": "text", "text": "\n".join(lines)}],
        }

    async def _search_tavily(self, query: str) -> dict[str, Any]:
        import httpx

        api_key = os.environ.get("TAVILY_API_KEY")
        if not api_key:
            return self._error("Tavily 搜索需要 TAVILY_API_KEY 环境变量;请设置后重试,或切换 backend 为 duckduckgo")

        payload = {
            "query": query,
            "max_results": self.max_results,
            "include_answer": self.include_answer,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.tavily.com/search",
                    json=payload,
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as e:
            return self._error(f"Tavily API 错误: {e.response.status_code} {e.response.text[:200]}")
        except httpx.RequestError as e:
            return self._error(f"Tavily 请求失败: {e}")
        except Exception as e:
            return self._error(f"Tavily 搜索失败: {e}")

        answer = data.get("answer", "")
        results = data.get("results", [])

        lines = [f"🔍 {query}", ""]
        if answer:
            lines.append(f"💡 答案: {answer}")
            lines.append("")

        for i, r in enumerate(results, 1):
            title = r.get("title", "(无标题)")
            url = r.get("url", "")
            content = r.get("content", "")
            lines.append(f"{i}. {title}")
            lines.append(f"   {url}")
            if content:
                lines.append(f"   {content[:200]}")
            lines.append("")

        return {
            "type": "tool_result",
            "content": [{"type": "text", "text": "\n".join(lines)}],
        }

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {
            "type": "tool_result",
            "content": [{"type": "text", "text": message}],
            "is_error": True,
        }

"""Reference resolver 注册表 + `ReferenceExpander`。

`ReferenceExpander.expand(messages)`:扫每条 user message,把 `@(file|diff|url|
session):<key>` 替换成
`<reference type=X key=Y>content</reference>`(或 `error=...` 当 resolver 失败);
非 user 消息原样返回。

并发:同一条消息内的多个 @reference 并发 resolve(asyncio.gather)。
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chariot.agent.chat_request import Message
from chariot.context.references.base import (
    BaseReferenceResolver,
    ReferenceResolveError,
    ResolveContext,
    ResolvedReference,
)
from chariot.context.references.builtin.diff import DiffReferenceResolver
from chariot.context.references.builtin.file import FileReferenceResolver
from chariot.context.references.builtin.session import SessionReferenceResolver
from chariot.context.references.builtin.url import UrlReferenceResolver

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


REFERENCE_RESOLVERS: dict[str, type[BaseReferenceResolver]] = {
    FileReferenceResolver.type_id: FileReferenceResolver,
    DiffReferenceResolver.type_id: DiffReferenceResolver,
    UrlReferenceResolver.type_id: UrlReferenceResolver,
    SessionReferenceResolver.type_id: SessionReferenceResolver,
}


# 匹配 @file:key / @diff:key / @url:key / @session:key
# 冒号后允许出现若干空白,兼容 `@url: https://...` 这类更自然的写法。
# key 字符集:任何非空白(简单宽松;具体安全校验在 resolver 内部做)
_REFERENCE_PATTERN = re.compile(
    r"@(?P<type>file|url|session):\s*(?P<key>\S*)|@(?P<diff_type>diff)(?::\s*(?P<diff_key>\S*))?",
)


class ReferenceExpander:
    """user message 里的 @reference 扩展。

    用法::

        expander = ReferenceExpander(cwd=Path.cwd(), allowed_domains=frozenset(),
                                      sessionmaker=sm)
        new_messages = await expander.expand(req.messages)
    """

    def __init__(
        self,
        *,
        cwd: Path,
        allowed_domains: frozenset[str],
        sessionmaker: async_sessionmaker[AsyncSession] | None = None,
        max_bytes: int = 65536,
        resolvers: dict[str, BaseReferenceResolver] | None = None,
    ) -> None:
        self._ctx = ResolveContext(
            cwd=cwd,
            allowed_domains=allowed_domains,
            sessionmaker=sessionmaker,
            max_bytes=max_bytes,
        )
        # 单例化 resolver 实例(无状态);测试可注入 stub
        self._resolvers: dict[str, BaseReferenceResolver] = resolvers or {
            type_id: cls() for type_id, cls in REFERENCE_RESOLVERS.items()
        }

    async def expand(self, messages: list[Message]) -> list[Message]:
        """并发解析每条 user message 的 @reference;非 user / 无引用 → 原样。"""
        out: list[Message] = []
        for msg in messages:
            if msg.role != "user":
                out.append(msg)
                continue
            new_msg = await self._expand_message(msg)
            out.append(new_msg)
        return out

    async def _expand_message(self, msg: Message) -> Message:
        if isinstance(msg.content, str):
            new_text = await self._expand_text(msg.content)
            if new_text == msg.content:
                return msg
            return dataclasses.replace(msg, content=new_text)
        new_blocks: list[dict[str, Any]] = []
        changed = False
        for block in msg.content:
            if not isinstance(block, dict):
                new_blocks.append(block)
                continue
            if block.get("type") != "text":
                new_blocks.append(block)
                continue
            text = str(block.get("text", ""))
            new_text = await self._expand_text(text)
            if new_text != text:
                changed = True
            new_block = dict(block)
            new_block["text"] = new_text
            new_blocks.append(new_block)
        if not changed:
            return msg
        return dataclasses.replace(msg, content=new_blocks)

    async def _expand_text(self, text: str) -> str:
        matches = list(_REFERENCE_PATTERN.finditer(text))
        if not matches:
            return text
        results = await asyncio.gather(*(self._resolve_match(m) for m in matches))
        # 替换逆序避免下标漂移
        out = text
        for m, replacement in zip(reversed(matches), reversed(results), strict=True):
            out = out[: m.start()] + replacement + out[m.end() :]
        return out

    async def _resolve_match(self, match: re.Match[str]) -> str:
        type_id = match.group("type") or match.group("diff_type") or ""
        key = match.group("key") or match.group("diff_key") or ""
        return await self._resolve_one(type_id, key)

    async def _resolve_one(self, type_id: str, key: str) -> str:
        resolver = self._resolvers.get(type_id)
        if resolver is None:
            return self._format_error(type_id, key, "unknown_type")
        try:
            ref = await resolver.resolve(key, self._ctx)
        except ReferenceResolveError as e:
            return self._format_error(e.type_id, e.key, e.reason, detail=e.detail)
        except Exception as e:  # pragma: no cover - 兜底
            return self._format_error(type_id, key, "internal_error", detail=str(e))
        return self._format_success(ref)

    @staticmethod
    def _format_success(ref: ResolvedReference) -> str:
        attrs = f'type="{ref.type_id}" key="{_xml_escape(ref.key)}"'
        if ref.truncated:
            attrs += ' truncated="true"'
        return f"<reference {attrs}>\n{ref.content}\n</reference>"

    @staticmethod
    def _format_error(type_id: str, key: str, reason: str, *, detail: str | None = None) -> str:
        attrs = f'type="{type_id}" key="{_xml_escape(key)}" error="{reason}"'
        if detail:
            attrs += f' detail="{_xml_escape(detail)}"'
        return f"<reference {attrs}></reference>"


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

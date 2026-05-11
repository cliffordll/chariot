"""Reference resolver ABC + 上下文 / 结果数据对象。

设计契约:
- `BaseReferenceResolver`:每个 type_id(file/diff/url/session)一个子类;无状态
- `resolve(key, ctx)`:返 `ResolvedReference`(content + truncated 标记)
- 失败语义:抛 `ReferenceResolveError`,`ReferenceExpander` 捕获后嵌
  `<reference type=X key=Y error="reason"></reference>`,**不静默丢失**(让 agent
  知道用户原本想引用什么)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(frozen=True)
class ResolveContext:
    """解析时需要的环境句柄。

    - `cwd`:`@file:` / `@diff:` 的根路径(默认进程 cwd;tests 可注入 tmp_path)
    - `allowed_domains`:`@url:` 白名单(host lowercase set)
    - `sessionmaker`:`@session:` 用,读 conversations / messages 表
    - `max_bytes`:任何 resolver 返回 content 的上限,过限标 truncated=True
    """

    cwd: Path
    allowed_domains: frozenset[str]
    sessionmaker: async_sessionmaker[AsyncSession] | None
    max_bytes: int = 65536


@dataclass(frozen=True)
class ResolvedReference:
    """解析成功返回。"""

    type_id: str
    key: str
    content: str
    truncated: bool = False


class ReferenceResolveError(Exception):
    """resolver 失败语义;`ReferenceExpander` 捕获后写成 error reference block。

    `reason`:简短错误标识(`not_found` / `path_traversal` / `disallowed_host` /
    `timeout` / `subprocess_error` / `internal_error`)。
    """

    def __init__(self, reason: str, *, type_id: str, key: str, detail: str | None = None) -> None:
        self.reason = reason
        self.type_id = type_id
        self.key = key
        self.detail = detail
        super().__init__(detail or reason)


class BaseReferenceResolver(ABC):
    type_id: ClassVar[str]

    @abstractmethod
    async def resolve(self, key: str, ctx: ResolveContext) -> ResolvedReference: ...

    # ---- 通用工具方法(给子类用) ----

    @staticmethod
    def _truncate(content: str, max_bytes: int) -> tuple[str, bool]:
        """按 utf-8 字节长度截断;返 (clipped, truncated)。"""
        encoded = content.encode("utf-8")
        if len(encoded) <= max_bytes:
            return content, False
        clipped = encoded[:max_bytes].decode("utf-8", errors="ignore")
        return clipped, True

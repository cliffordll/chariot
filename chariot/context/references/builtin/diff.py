"""`@diff:<ref>` resolver(B3 wave 3)。

`key` 形态:
- 空 / 'HEAD'      → `git diff HEAD`(uncommitted + staged)
- `HEAD~3`         → `git diff HEAD~3..HEAD`
- `<sha>..<sha2>`  → 透传
- `<sha>`          → `git diff <sha>`

Security 红线:
- subprocess 跑 git,超时 10s
- output 截到 `ctx.max_bytes`
- cwd 在 `ctx.cwd` 下(防 agent 跨工程跑 git)
- 不允许 shell metachar(空格分隔的 git ref 直接当 argv 参数,不进 shell)
"""

from __future__ import annotations

import asyncio
import re
from typing import ClassVar

from chariot.context.references.base import (
    BaseReferenceResolver,
    ReferenceResolveError,
    ResolveContext,
    ResolvedReference,
)

_DEFAULT_TIMEOUT_S = 10.0
# git ref:字母数字 + _-./~^ + 数字范围:仅保守字符集合,拒 shell metachar
_REF_PATTERN = re.compile(r"^[A-Za-z0-9_\-./~^.]+$")


class DiffReferenceResolver(BaseReferenceResolver):
    type_id: ClassVar[str] = "diff"

    async def resolve(self, key: str, ctx: ResolveContext) -> ResolvedReference:
        argv = self._build_argv(key)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(ctx.cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_DEFAULT_TIMEOUT_S)
            except TimeoutError as e:
                proc.kill()
                await proc.wait()
                raise ReferenceResolveError("timeout", type_id=self.type_id, key=key) from e
        except FileNotFoundError as e:
            raise ReferenceResolveError(
                "subprocess_error",
                type_id=self.type_id,
                key=key,
                detail="git executable not found",
            ) from e
        if proc.returncode != 0:
            err_text = stderr.decode("utf-8", errors="ignore").strip() if stderr else ""
            raise ReferenceResolveError(
                "subprocess_error",
                type_id=self.type_id,
                key=key,
                detail=err_text or f"git exit {proc.returncode}",
            )
        text = stdout.decode("utf-8", errors="ignore") if stdout else ""
        clipped, truncated = self._truncate(text, ctx.max_bytes)
        return ResolvedReference(type_id=self.type_id, key=key or "HEAD", content=clipped, truncated=truncated)

    @classmethod
    def _build_argv(cls, key: str) -> list[str]:
        """构造 git argv;拒 shell metachar / 空格(防 argv 注入)。"""
        argv: list[str] = ["git", "diff", "--no-color"]
        if not key:
            return argv  # uncommitted diff
        # 支持 "A..B" / "A...B" / 单 ref
        for piece in cls._split_refs(key):
            if not _REF_PATTERN.match(piece):
                raise ReferenceResolveError(
                    "invalid_ref",
                    type_id="diff",
                    key=key,
                    detail=f"piece {piece!r} rejected",
                )
        argv.append(key)
        return argv

    @staticmethod
    def _split_refs(key: str) -> list[str]:
        """把 'A..B' / 'A...B' / 'A' 切成单个 ref 验证;`..` / `...` 是分隔符。"""
        if "..." in key:
            return key.split("...", 1)
        if ".." in key:
            return key.split("..", 1)
        return [key]

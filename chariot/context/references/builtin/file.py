"""`@file:<path>` resolver(B3 wave 3)。

Security 红线:
- path 必须在 `ctx.cwd` 子树下;`..` 跨出 → `path_traversal`
- 绝对路径 → 必须以 cwd 开头,否则 `path_traversal`
- 符号链接 → resolve 后再校验 cwd 关系(防 `ln -s /etc/passwd inside.txt`)
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from chariot.context.references.base import (
    BaseReferenceResolver,
    ReferenceResolveError,
    ResolveContext,
    ResolvedReference,
)


class FileReferenceResolver(BaseReferenceResolver):
    type_id: ClassVar[str] = "file"

    async def resolve(self, key: str, ctx: ResolveContext) -> ResolvedReference:
        if not key:
            raise ReferenceResolveError("empty_key", type_id=self.type_id, key=key)
        target = self._resolve_safe_path(key, ctx.cwd)
        if not target.exists():
            raise ReferenceResolveError("not_found", type_id=self.type_id, key=key, detail=str(target))
        if not target.is_file():
            raise ReferenceResolveError("not_a_file", type_id=self.type_id, key=key)
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            raise ReferenceResolveError(
                "binary_file",
                type_id=self.type_id,
                key=key,
                detail=f"not utf-8: {e}",
            ) from e
        clipped, truncated = self._truncate(text, ctx.max_bytes)
        return ResolvedReference(type_id=self.type_id, key=key, content=clipped, truncated=truncated)

    @staticmethod
    def _resolve_safe_path(key: str, cwd: Path) -> Path:
        """禁 `..` 跨出 cwd;绝对路径必须在 cwd 子树。"""
        cwd_resolved = cwd.resolve()
        candidate = Path(key)
        if not candidate.is_absolute():
            candidate = cwd_resolved / candidate
        try:
            resolved = candidate.resolve(strict=False)
        except OSError as e:
            raise ReferenceResolveError(
                "internal_error",
                type_id="file",
                key=key,
                detail=str(e),
            ) from e
        try:
            resolved.relative_to(cwd_resolved)
        except ValueError as e:
            raise ReferenceResolveError(
                "path_traversal",
                type_id="file",
                key=key,
                detail=f"{resolved} not under {cwd_resolved}",
            ) from e
        return resolved

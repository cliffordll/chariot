"""`@session:<id|prefix>` resolver(B3 wave 3)。

`key` 形态:
- 完整 ULID(26 char,case-insensitive)→ 精确匹配
- prefix(>= 4 char)→ 找唯一前缀匹配(多匹配 → `ambiguous_prefix`)

Security 红线:
- chariot 当前是单用户;未来多租户时这里加 owner_id 过滤
- 拉取 messages 时按 `ctx.max_bytes` 截 content,避免巨大对话灌进 prompt
"""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import select

from chariot.context.references.base import (
    BaseReferenceResolver,
    ReferenceResolveError,
    ResolveContext,
    ResolvedReference,
)
from chariot.database.models import ConversationRow, MessageRow


class SessionReferenceResolver(BaseReferenceResolver):
    type_id: ClassVar[str] = "session"

    async def resolve(self, key: str, ctx: ResolveContext) -> ResolvedReference:
        if not key:
            raise ReferenceResolveError("empty_key", type_id=self.type_id, key=key)
        if ctx.sessionmaker is None:
            raise ReferenceResolveError(
                "no_sessionmaker",
                type_id=self.type_id,
                key=key,
                detail="sessionmaker not provided",
            )
        async with ctx.sessionmaker() as session:
            conversation_id = await self._resolve_id(key, session)
            messages_text = await self._collect_messages_text(conversation_id, session, max_bytes=ctx.max_bytes)
        return messages_text

    async def _resolve_id(self, key: str, session) -> str:  # type: ignore[no-untyped-def]
        # 精确匹配优先(快路径)
        exact = await session.execute(select(ConversationRow).where(ConversationRow.id == key))
        if (row := exact.scalar_one_or_none()) is not None:
            return row.id
        if len(key) < 4:
            raise ReferenceResolveError(
                "prefix_too_short",
                type_id=self.type_id,
                key=key,
                detail="need >= 4 chars for prefix lookup",
            )
        stmt = select(ConversationRow).where(ConversationRow.id.like(f"{key}%"))
        rows = (await session.execute(stmt)).scalars().all()
        if not rows:
            raise ReferenceResolveError("not_found", type_id=self.type_id, key=key)
        if len(rows) > 1:
            raise ReferenceResolveError(
                "ambiguous_prefix",
                type_id=self.type_id,
                key=key,
                detail=f"{len(rows)} matches",
            )
        return rows[0].id

    async def _collect_messages_text(
        self,
        conversation_id: str,
        session,
        *,
        max_bytes: int,  # type: ignore[no-untyped-def]
    ) -> ResolvedReference:
        stmt = select(MessageRow).where(MessageRow.conversation_id == conversation_id).order_by(MessageRow.seq.asc())
        rows = (await session.execute(stmt)).scalars().all()
        lines: list[str] = []
        for row in rows:
            lines.append(f"{row.role}: {row.content}")
        full_text = "\n\n".join(lines)
        clipped, truncated = self._truncate(full_text, max_bytes)
        return ResolvedReference(
            type_id=self.type_id,
            key=conversation_id,
            content=clipped,
            truncated=truncated,
        )

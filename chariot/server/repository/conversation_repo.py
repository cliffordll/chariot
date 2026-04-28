"""ConversationRepo:`conversations` + `messages` 两张表的数据访问层(0.4.0)。

职责
----
- conversations CRUD:create / get / list / delete / update_title
- ensure_exists:不存在则插入(供 dataplane auto-create 模式 A 用)
- append_message:写一条 message,`seq` 内部 SELECT MAX 自增;若 role='assistant'
  顺带更新 `conversations.last_model`
- load_messages_as_anthropic:SELECT + reshape 成 Anthropic 协议 messages 数组
  形态 `[{role, content}, ...]`(content 已经是协议原生 blocks,丢掉
  seq / model_name 等元数据列即可)

不暴露的事
----------
- ULID 生成:controller 层负责(`POST /admin/conversations` 显式创建用 server
  生成,模式 A 由 client 自带);repo 接受任意非空字符串作 id
- ID 校验:controller 层做(正则 `^[0-9A-Z]{26}$`);repo 不重复校验

cascade delete 不依赖 SQLite PRAGMA foreign_keys —— `delete()` 手动 DELETE FROM
messages,行为不被全局开关影响。

模块级零自由函数,所有逻辑收在 `ConversationRepo` 类里。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.server.config import ConfigError
from chariot.server.database.models import ConversationRow, MessageRow


@dataclass(frozen=True)
class Conversation:
    """conversations 表行的数据形态(0.4.0)。

    给 controller 层做响应序列化用(ORM row 不直接出层)。
    `message_count` 由 list / get 时 JOIN 算出,append 后过期。
    """

    id: str
    title: str | None
    last_model: str | None
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class ConversationRepo:
    """`conversations` + `messages` 表的数据访问层。"""

    # role 枚举(Anthropic 协议原生,对齐 Claude Code transcript)
    ROLE_USER = "user"
    ROLE_ASSISTANT = "assistant"
    _VALID_ROLES = frozenset({ROLE_USER, ROLE_ASSISTANT})

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- conversations CRUD ----

    async def create(self, conv_id: str, *, title: str | None = None) -> Conversation:
        """显式创建一个 conversation。id 已存在 → ConfigError(controller 转 409)。"""
        if not conv_id:
            raise ConfigError("conversation id 必须是非空字符串")
        existing = await self._find_row(conv_id)
        if existing is not None:
            raise ConfigError(f"conversation id {conv_id!r} 已存在")
        row = ConversationRow(id=conv_id, title=title, last_model=None)
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return self._row_to_conv(row, message_count=0)

    async def ensure_exists(self, conv_id: str) -> Conversation:
        """不存在则插入(模式 A auto-create);存在则返当前形态。"""
        if not conv_id:
            raise ConfigError("conversation id 必须是非空字符串")
        row = await self._find_row(conv_id)
        if row is None:
            row = ConversationRow(id=conv_id, title=None, last_model=None)
            self.session.add(row)
            await self.session.commit()
            await self.session.refresh(row)
            return self._row_to_conv(row, message_count=0)
        return await self._with_message_count(row)

    async def get(self, conv_id: str) -> Conversation | None:
        row = await self._find_row(conv_id)
        if row is None:
            return None
        return await self._with_message_count(row)

    async def list_entries(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Conversation]:
        """按 updated_at desc 列出。GUI 侧栏 / admin/conversations GET 用。"""
        stmt = (
            select(ConversationRow)
            .order_by(ConversationRow.updated_at.desc(), ConversationRow.id.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [await self._with_message_count(r) for r in rows]

    async def update_title(self, conv_id: str, title: str | None) -> Conversation:
        row = await self._find_row(conv_id)
        if row is None:
            raise ConfigError(f"未知 conversation id: {conv_id!r}")
        row.title = title
        await self.session.commit()
        await self.session.refresh(row)
        return await self._with_message_count(row)

    async def delete(self, conv_id: str) -> None:
        """删 conversation + 手动 cascade 删 messages(不依赖 SQLite FK 开关)。"""
        row = await self._find_row(conv_id)
        if row is None:
            raise ConfigError(f"未知 conversation id: {conv_id!r}")
        await self.session.execute(
            sa_delete(MessageRow).where(MessageRow.conversation_id == conv_id),
        )
        await self.session.delete(row)
        await self.session.commit()

    # ---- messages 写 / 读 ----

    async def append_message(
        self,
        conv_id: str,
        role: str,
        content: str | list[dict[str, Any]],
        *,
        model_name: str | None = None,
    ) -> MessageRow:
        """追加一条 message。`seq` 内部 SELECT MAX+1;role='assistant' 时同步更新
        `conversations.last_model`(如果给了 model_name)。

        - `content`:字符串(纯文本)或 anthropic content blocks 数组,原样 JSON 序列化
        - role 必须 ∈ {'user', 'assistant'}(协议原生),否则 ConfigError
        - 调用方需保证 conv_id 已存在(否则 message 成"孤儿",但不报错)
        """
        if role not in self._VALID_ROLES:
            raise ConfigError(
                f"messages.role 必须 ∈ {sorted(self._VALID_ROLES)},得到 {role!r}",
            )
        if role != self.ROLE_ASSISTANT and model_name is not None:
            raise ConfigError(
                f"model_name 仅 role='assistant' 可填,得到 role={role!r}",
            )

        next_seq = await self._next_seq(conv_id)
        content_json = self._serialize_content(content)
        msg = MessageRow(
            conversation_id=conv_id,
            seq=next_seq,
            role=role,
            content=content_json,
            model_name=model_name if role == self.ROLE_ASSISTANT else None,
        )
        self.session.add(msg)

        # 派生:assistant 行更新 conversations.last_model + 撞 updated_at
        # 任何写都更新 updated_at(让 GUI 列表按"最近活跃"排序)
        conv = await self._find_row(conv_id)
        if conv is not None:
            if role == self.ROLE_ASSISTANT and model_name is not None:
                conv.last_model = model_name
            # SQLAlchemy onupdate 只在 conv 本身的列被改写时触发;为了保证即使
            # user 行写入也撞 updated_at,这里显式赋值。语义跟 ORM `_utcnow`
            # 一致(UTC,naive 一致性):chariot 全仓 datetime 都是 UTC
            conv.updated_at = datetime.now(UTC)

        await self.session.commit()
        await self.session.refresh(msg)
        return msg

    async def load_messages_as_anthropic(self, conv_id: str) -> list[dict[str, Any]]:
        """按 seq 升序读出,reshape 成 Anthropic 协议 messages 数组形态。

        返回 `[{role, content}, ...]`,content 已经是协议原生(string 或 blocks
        数组),tool_use / tool_result 嵌在对应 role 的 content blocks 里。
        Agent 把这个数组 prepend 到客户端这次发的 body.messages 前再调 Model。
        """
        stmt = (
            select(MessageRow)
            .where(MessageRow.conversation_id == conv_id)
            .order_by(MessageRow.seq.asc())
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [{"role": r.role, "content": self._deserialize_content(r.content)} for r in rows]

    async def list_messages(self, conv_id: str) -> list[MessageRow]:
        """返原始 ORM rows(给 admin/conversations/{id} 详情用 —— 需要 seq /
        model_name / created_at 等元数据)。"""
        stmt = (
            select(MessageRow)
            .where(MessageRow.conversation_id == conv_id)
            .order_by(MessageRow.seq.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    # ---- 内部 ----

    async def _find_row(self, conv_id: str) -> ConversationRow | None:
        stmt = select(ConversationRow).where(ConversationRow.id == conv_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _next_seq(self, conv_id: str) -> int:
        """SELECT MAX(seq) + 1;表内无该 conv 的 message 返 0。"""
        stmt = select(func.max(MessageRow.seq)).where(
            MessageRow.conversation_id == conv_id,
        )
        current = await self.session.scalar(stmt)
        if current is None:
            return 0
        return int(current) + 1

    async def _message_count(self, conv_id: str) -> int:
        stmt = select(func.count(MessageRow.id)).where(
            MessageRow.conversation_id == conv_id,
        )
        n = await self.session.scalar(stmt)
        return int(n or 0)

    async def _with_message_count(self, row: ConversationRow) -> Conversation:
        return self._row_to_conv(row, message_count=await self._message_count(row.id))

    @staticmethod
    def _row_to_conv(row: ConversationRow, *, message_count: int) -> Conversation:
        return Conversation(
            id=row.id,
            title=row.title,
            last_model=row.last_model,
            created_at=row.created_at,
            updated_at=row.updated_at,
            message_count=message_count,
        )

    @staticmethod
    def _serialize_content(content: str | list[dict[str, Any]]) -> str:
        try:
            return json.dumps(content, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            raise ConfigError(f"message content 不可 JSON 序列化: {e}") from e

    @staticmethod
    def _deserialize_content(raw: str) -> str | list[dict[str, Any]]:
        """anthropic content 可以是 string 或 list[dict],原样还原。"""
        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ConfigError(f"message content JSON 损坏: {e}") from e
        if isinstance(data, str):
            return data
        if isinstance(data, list):
            return cast(list[dict[str, Any]], data)
        raise ConfigError(
            f"message content JSON 顶层必须是 string 或 array,得到 {type(data).__name__}",
        )

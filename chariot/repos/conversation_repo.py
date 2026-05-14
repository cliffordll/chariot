"""ConversationRepo:`conversations` + `messages` 两张表的数据访问层(0.4.0)。

v5(0.6.0)起表名 `conversations` rename → `conversations`,字段
`messages.conversation_id` rename → `conversation_id`(跨层缩写统一,详见 DESIGN.md §7.1)。

职责
----
- conversations CRUD:create / get / list / delete / update_title
- ensure_exists:不存在则插入(供 dataplane auto-create 模式 A 用)
- append_message:写一条 message,`seq` 内部 SELECT MAX 自增;若 role='assistant'
  顺带更新 `conversations.last_provider`
- load_messages_as_anthropic:SELECT + reshape 成 Anthropic 协议 messages 数组
  形态 `[{role, content}, ...]`(content 已经是协议原生 blocks,丢掉
  seq / provider_name 等元数据列即可)

不暴露的事
----------
- ULID 生成:surface 层负责(sidecar 路径下 client 自带 conversation_id,首次 chat
  时 ensure_exists);repo 接受任意非空字符串作 id
- ID 校验:surface 层做(正则 `^[0-9A-Z]{26}$`);repo 不重复校验

cascade delete 不依赖 SQLite PRAGMA foreign_keys —— `delete()` 手动 DELETE FROM
messages,行为不被全局开关影响。

模块级零自由函数,所有逻辑收在 `ConversationRepo` 类里。
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.config import (
    ConfigError,
    ConversationNotFound,
    DuplicateConversationId,
)
from chariot.agent.exceptions import ConversationLockTimeout
from chariot.database.models import ConversationRow, MessageRow


@dataclass(frozen=True)
class Conversation:
    """conversations 表行的数据形态(v5)。

    给 controller 层做响应序列化用(ORM row 不直接出层)。
    `message_count` 由 list / get 时 JOIN 算出,append 后过期。
    """

    id: str
    title: str | None
    last_provider: str | None
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    agent_profile: str | None = None


@dataclass(frozen=True)
class MessageSearchHit:
    """FTS5 搜索单条命中(v17 起)。

    - `snippet`:`<mark>` 包住命中词的 ±N token 片段;直接渲染前端高亮
    - `rank`:bm25 分数(越小越好,SQLite FTS5 惯例)
    """

    message_id: str
    conversation_id: str
    role: str
    snippet: str
    rank: float


class ConversationRepo:
    """`conversations` + `messages` 表的数据访问层。"""

    # role 枚举(Anthropic 协议原生,对齐 Claude Code transcript)
    ROLE_USER = "user"
    ROLE_ASSISTANT = "assistant"
    _VALID_ROLES = frozenset({ROLE_USER, ROLE_ASSISTANT})

    # SQLite advisory lock 默认参数
    _DEFAULT_DB_LOCK_TIMEOUT_S = 5.0
    _DEFAULT_DB_LOCK_MAX_RETRIES = 3

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- 跨进程 advisory lock(0.6.0+,详见 DESIGN §7.2) ----

    @asynccontextmanager
    async def with_advisory_lock(
        self,
        conversation_id: str,
        *,
        timeout_s: float | None = None,
        max_retries: int | None = None,
    ) -> AsyncGenerator[None, None]:
        """SQLite advisory lock 包 critical section(load history + append message)。

        实现:每次 acquire 在本 session 上跑 `BEGIN IMMEDIATE`(获 RESERVED
        锁,允许 read 禁其它写),立即 `ROLLBACK` 释放锁;然后调用方自己跑
        critical section(进程内 asyncio.Lock 已经保证同进程串行,DB 锁只防
        跨进程)。`busy_timeout` 控制等待时长,失败自动重试 `max_retries`
        次,再失败抛 `ConversationLockTimeout(layer="db")`。

        **设计取舍**(详见 DESIGN §7.2):
        - 不持锁包整段 critical section —— SQLAlchemy session 内部各 repo
          方法自己 commit,跟 outer BEGIN/COMMIT 嵌套冲突
        - 改用"探测一次 RESERVED 锁可获 → 立即释放"模式:跨进程并发的两个
          writer 会被 SQLite 内部 busy_timeout 串行;同进程并发由 asyncio.Lock
          串行(0.5.0 行为)
        - 严格 atomic"load + write"由调用方(AgentLoop)用短事务保证

        **NOTE**:`conversation_id` 仅作 logging 标识,SQLite advisory lock 是**整库
        级别**(BEGIN IMMEDIATE 锁全库),不是 row-level。chariot 短事务用法
        下问题不大;若多 conversation 高频并发竞争,可改用 row-level 锁(SQLite 没原生
        支持,得自己拼 + 自旋,留作 0.10.0+ 优化项)。
        """
        eff_timeout = timeout_s if timeout_s is not None else self._DEFAULT_DB_LOCK_TIMEOUT_S
        eff_retries = max_retries if max_retries is not None else self._DEFAULT_DB_LOCK_MAX_RETRIES
        last_busy_err: Exception | None = None
        acquired = False
        for _attempt in range(eff_retries):
            try:
                await self.session.execute(text(f"PRAGMA busy_timeout = {int(eff_timeout * 1000)}"))
                await self.session.execute(text("BEGIN IMMEDIATE"))
                # 探测成功 —— 立即 ROLLBACK 释放(否则后续 repo 方法自己 commit
                # 会跟此事务冲突);跨进程并发的另一 writer 等过 busy_timeout 失败
                await self.session.execute(text("ROLLBACK"))
                acquired = True
                break
            except OperationalError as e:
                if self._is_busy_error(e):
                    last_busy_err = e
                    continue
                raise

        if not acquired:
            raise ConversationLockTimeout(
                f"conversation {conversation_id} DB advisory lock 等待超时"
                f"(busy_timeout={eff_timeout}s, 重试 {eff_retries} 次)",
                layer="db",
            ) from last_busy_err

        # critical section 由调用方运行,异常上抛(不在此处捕获)
        yield

    @staticmethod
    def _is_busy_error(exc: OperationalError) -> bool:
        """SQLite busy / locked 错误识别(各驱动 message 略不同)。"""
        msg = str(exc).lower()
        return "database is locked" in msg or "busy" in msg

    # ---- conversations CRUD ----

    async def create(self, conversation_id: str, *, title: str | None = None) -> Conversation:
        """显式创建一个 conversation。id 已存在 → DuplicateConversationId(409)。"""
        if not conversation_id:
            raise ConfigError("conversation id 必须是非空字符串")
        existing = await self._find_row(conversation_id)
        if existing is not None:
            raise DuplicateConversationId(f"conversation id {conversation_id!r} 已存在")
        row = ConversationRow(id=conversation_id, title=title, last_provider=None)
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return self._row_to_conversation(row, message_count=0)

    async def ensure_exists(self, conversation_id: str) -> Conversation:
        """不存在则插入(模式 A auto-create);存在则返当前形态。"""
        if not conversation_id:
            raise ConfigError("conversation id 必须是非空字符串")
        row = await self._find_row(conversation_id)
        if row is None:
            row = ConversationRow(id=conversation_id, title=None, last_provider=None)
            self.session.add(row)
            await self.session.commit()
            await self.session.refresh(row)
            return self._row_to_conversation(row, message_count=0)
        return await self._with_message_count(row)

    async def get(self, conversation_id: str) -> Conversation | None:
        row = await self._find_row(conversation_id)
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

    async def update_title(self, conversation_id: str, title: str | None) -> Conversation:
        row = await self._find_row(conversation_id)
        if row is None:
            raise ConversationNotFound(f"未知 conversation id: {conversation_id!r}")
        row.title = title
        await self.session.commit()
        await self.session.refresh(row)
        return await self._with_message_count(row)

    async def delete(self, conversation_id: str) -> None:
        """删 conversation + 手动 cascade 删 messages + 清 FTS5 索引。"""
        row = await self._find_row(conversation_id)
        if row is None:
            raise ConversationNotFound(f"未知 conversation id: {conversation_id!r}")
        # v17:同步清 FTS5(参数化绑定避 SQL injection)
        await self.session.execute(
            text("DELETE FROM messages_fts WHERE conversation_id = :cid"),
            {"cid": conversation_id},
        )
        await self.session.execute(
            sa_delete(MessageRow).where(MessageRow.conversation_id == conversation_id),
        )
        await self.session.delete(row)
        await self.session.commit()

    # ---- messages 写 / 读 ----

    async def append_message(
        self,
        conversation_id: str,
        role: str,
        content: str | list[dict[str, Any]],
        *,
        provider_name: str | None = None,
        agent_profile: str | None = None,
    ) -> MessageRow:
        """追加一条 message。`seq` 内部 SELECT MAX+1;role='assistant' 时同步更新
        `conversations.last_provider`(如果给了 provider_name)。

        - `content`:字符串(纯文本)或 anthropic content blocks 数组,原样 JSON 序列化
        - role 必须 ∈ {'user', 'assistant'}(协议原生),否则 ConfigError
        - 调用方需保证 conversation_id 已存在(否则 message 成"孤儿",但不报错)
        """
        if role not in self._VALID_ROLES:
            raise ConfigError(
                f"messages.role 必须 ∈ {sorted(self._VALID_ROLES)},得到 {role!r}",
            )
        if role != self.ROLE_ASSISTANT and provider_name is not None:
            raise ConfigError(
                f"provider_name 仅 role='assistant' 可填,得到 role={role!r}",
            )

        next_seq = await self._next_seq(conversation_id)
        content_json = self._serialize_content(content)
        msg = MessageRow(
            conversation_id=conversation_id,
            seq=next_seq,
            role=role,
            content=content_json,
            provider_name=provider_name if role == self.ROLE_ASSISTANT else None,
        )
        self.session.add(msg)
        # 必须 flush 才能拿到 msg.id(default=_new_ulid 在 flush 时填),
        # FTS 同步需要 id 作 message_id
        await self.session.flush()
        # v17:同步索引到 FTS5。content 字段存 text-only(从 anthropic blocks 抽),
        # 而非原始 JSON,避免 'type' / 'text' 这种 schema 关键字成噪声词
        fts_content = self._extract_indexable_text(content)
        await self.session.execute(
            text(
                "INSERT INTO messages_fts (message_id, conversation_id, role, content) "
                "VALUES (:mid, :cid, :role, :content)",
            ),
            {"mid": msg.id, "cid": conversation_id, "role": role, "content": fts_content},
        )

        # 派生:assistant 行更新 conversations.last_provider + agent_profile + 撞 updated_at
        # 任何写都更新 updated_at(让 GUI 列表按"最近活跃"排序)
        conversation = await self._find_row(conversation_id)
        if conversation is not None:
            if role == self.ROLE_ASSISTANT and provider_name is not None:
                conversation.last_provider = provider_name
            if role == self.ROLE_ASSISTANT and agent_profile is not None:
                conversation.agent_profile = agent_profile
            # SQLAlchemy onupdate 只在 conversation 本身的列被改写时触发;为了保证即使
            # user 行写入也撞 updated_at,这里显式赋值。语义跟 ORM `_utcnow`
            # 一致(UTC,naive 一致性):chariot 全仓 datetime 都是 UTC
            conversation.updated_at = datetime.now(UTC)

        await self.session.commit()
        await self.session.refresh(msg)
        return msg

    async def load_messages_as_anthropic(self, conversation_id: str) -> list[dict[str, Any]]:
        """按 seq 升序读出,reshape 成 Anthropic 协议 messages 数组形态。

        返回 `[{role, content}, ...]`,content 已经是协议原生(string 或 blocks
        数组),tool_use / tool_result 嵌在对应 role 的 content blocks 里。
        Agent 把这个数组 prepend 到客户端这次发的 body.messages 前再调 Model。
        """
        stmt = select(MessageRow).where(MessageRow.conversation_id == conversation_id).order_by(MessageRow.seq.asc())
        rows = (await self.session.execute(stmt)).scalars().all()
        return [{"role": r.role, "content": self._deserialize_content(r.content)} for r in rows]

    async def list_messages(self, conversation_id: str) -> list[MessageRow]:
        """返原始 ORM rows(给 admin/conversations/{id} 详情用 —— 需要 seq /
        provider_name / created_at 等元数据)。"""
        stmt = select(MessageRow).where(MessageRow.conversation_id == conversation_id).order_by(MessageRow.seq.asc())
        return list((await self.session.execute(stmt)).scalars().all())

    # ---- FTS5 全文搜索(v17 起) ----

    async def search(
        self,
        query: str,
        *,
        limit: int = 20,
        conversation_id: str | None = None,
    ) -> list[MessageSearchHit]:
        """跨 conversation 模糊查 messages。

        - `query` 是 FTS5 MATCH 表达式;最简形式直接传关键词,空格分隔多词等于 AND
          (`'hello world'` ≈ `'hello AND world'`)。SQLite FTS5 支持 `OR` / `NOT` /
          双引号短语 / `prefix*`,但 chariot 这层默认透传,不做语法糖
        - 默认按 bm25 排名(FTS5 内置),返 top-N
        - 给定 conversation_id 时,只查该会话内的 hit(给 stateful chat 内部找上下文用)
        - 返 MessageSearchHit:含 message_id / conversation_id / role / snippet / rank
        """
        if not query.strip():
            return []
        sql = (
            "SELECT message_id, conversation_id, role, "
            "snippet(messages_fts, 3, '<mark>', '</mark>', '…', 12) AS snippet, "
            "bm25(messages_fts) AS rank "
            "FROM messages_fts WHERE messages_fts MATCH :q"
        )
        params: dict[str, Any] = {"q": query, "limit": limit}
        if conversation_id is not None:
            sql += " AND conversation_id = :cid"
            params["cid"] = conversation_id
        sql += " ORDER BY rank LIMIT :limit"
        result = await self.session.execute(text(sql), params)
        hits: list[MessageSearchHit] = []
        for row in result.fetchall():
            hits.append(
                MessageSearchHit(
                    message_id=str(row.message_id),
                    conversation_id=str(row.conversation_id),
                    role=str(row.role),
                    snippet=str(row.snippet),
                    rank=float(row.rank),
                )
            )
        return hits

    async def rebuild_fts(self) -> int:
        """灾备命令:删空 messages_fts → 从 messages 表全量回填,返回回填行数。

        什么时候用:FTS 表跟 messages 不同步(测试漏调 sync / 早期版本 bug);用户
        手动跑 `chariot conversation rebuild-fts` 修复。
        """
        await self.session.execute(text("DELETE FROM messages_fts"))
        rows = await self.session.execute(select(MessageRow))
        count = 0
        for r in rows.scalars():
            fts_content = self._extract_indexable_text(self._deserialize_content(r.content))
            await self.session.execute(
                text(
                    "INSERT INTO messages_fts (message_id, conversation_id, role, content) "
                    "VALUES (:mid, :cid, :role, :content)",
                ),
                {"mid": r.id, "cid": r.conversation_id, "role": r.role, "content": fts_content},
            )
            count += 1
        await self.session.commit()
        return count

    @staticmethod
    def _extract_indexable_text(content: str | list[dict[str, Any]]) -> str:
        """从 anthropic content 抽 FTS5 该索引的文本。

        - 纯字符串 → 原样
        - blocks 数组 → 拼接所有 type=text 的 .text 字段,以及 type=tool_use 的
          .input(JSON 序列化)+ type=tool_result 的 content.text;其它类型跳过
        - 目的:不把 'type' / 'tool_use' / 'tool_result' 这种 anthropic schema 关键字
          灌进 FTS,降低 false positive
        """
        if isinstance(content, str):
            return content
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                t = block.get("text")
                if isinstance(t, str):
                    parts.append(t)
            elif btype == "tool_use":
                # tool_use input 是 dict;JSON dump 时 ensure_ascii=False 保中文
                inp = block.get("input")
                if isinstance(inp, dict):
                    parts.append(json.dumps(inp, ensure_ascii=False))
            elif btype == "tool_result":
                # tool_result.content 可能是 string 或 blocks 数组
                tr_content = block.get("content")
                if isinstance(tr_content, str):
                    parts.append(tr_content)
                elif isinstance(tr_content, list):
                    parts.append(ConversationRepo._extract_indexable_text(tr_content))
        return "\n".join(parts)

    # ---- 内部 ----

    async def _find_row(self, conversation_id: str) -> ConversationRow | None:
        stmt = select(ConversationRow).where(ConversationRow.id == conversation_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _next_seq(self, conversation_id: str) -> int:
        """SELECT MAX(seq) + 1;表内无该 conversation 的 message 返 0。"""
        stmt = select(func.max(MessageRow.seq)).where(
            MessageRow.conversation_id == conversation_id,
        )
        current = await self.session.scalar(stmt)
        if current is None:
            return 0
        return int(current) + 1

    async def _message_count(self, conversation_id: str) -> int:
        stmt = select(func.count(MessageRow.id)).where(
            MessageRow.conversation_id == conversation_id,
        )
        n = await self.session.scalar(stmt)
        return int(n or 0)

    async def _with_message_count(self, row: ConversationRow) -> Conversation:
        return self._row_to_conversation(row, message_count=await self._message_count(row.id))

    @staticmethod
    def _row_to_conversation(row: ConversationRow, *, message_count: int) -> Conversation:
        return Conversation(
            id=row.id,
            title=row.title,
            last_provider=row.last_provider,
            agent_profile=row.agent_profile,
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

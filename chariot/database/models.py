"""SQLAlchemy 声明式 ORM 模型。

与 `migrations/*.sql` 字段对齐;SQL 是 schema 真源,ORM 镜像它。

- v1:`logs` 表(请求流水)
- v2(0.3.0):`models` 表 + `settings`(KV)—— 模型配置 DB 化
- v3(0.3.1):`models` 加 `params` 列;drop `settings` 表(active 概念删除,
  client 在 body.model 写 entry name 直接路由)
- v4(0.4.0):加 `conversations / messages / tools` 三表 —— Conversation 层 +
  Tool 层。messages.role 仅 `user / assistant`(Anthropic 协议原生两种),
  tool_use / tool_result 嵌入 content blocks 数组;tools 4 条 seeded fixture

主键:
- `LogEntry.id` 是 32 字符 UUID4 hex(`default=` 插入时生成)
- `ModelRow.id` / `MessageRow.id` / `ToolRow.id` 是自增 int(name / id 才是用户面 ID)
- `ConversationRow.id` 是 26 字符 ULID 字符串(client 或 server 生成,校验在 controller 层)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from sqlalchemy import Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

LogStatus = Literal["ok", "error", "timeout"]


def _new_id() -> str:
    """32 字符 UUID4 hex(无连字符)。"""
    return uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class LogEntry(Base):
    __tablename__ = "logs"

    id: Mapped[str] = mapped_column(primary_key=True, default=_new_id)
    model: Mapped[str | None] = mapped_column(default=None)
    input_tokens: Mapped[int | None] = mapped_column(default=None)
    output_tokens: Mapped[int | None] = mapped_column(default=None)
    latency_ms: Mapped[int | None] = mapped_column(default=None)
    status: Mapped[str]
    error: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    __table_args__ = (Index("idx_logs_created_at", "created_at"),)


class ModelRow(Base):
    """`models` 表:0.3.0 起承载 [[models]] entries。

    - `options`:JSON,build Model 实例所需参数(model / api_key / base_url 等)
    - `params`:JSON,runtime sampling 默认值(temperature / top_p / max_tokens 等),
      0.3.1 加;客户端发请求时若 body 缺字段,前端从此处填(server 不主动注入)
    """

    __tablename__ = "models"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(unique=True, index=True)
    type: Mapped[str]
    options: Mapped[str]  # JSON-serialized dict
    params: Mapped[str]  # JSON-serialized dict;migration v3 列默认 '{}'
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class ConversationRow(Base):
    """`conversations` 表:0.4.0 多轮对话单元。

    - `id`:26 字符 ULID(client 或 server 生成,正则校验在 controller 层做);
      ULID 单调时间戳前缀让 `ORDER BY id` 即时间序
    - `title`:可选;空则 GUI 从首条 user msg 截取展示
    - `last_model`:派生字段,每写一轮 assistant msg 同步;仅供 GUI 侧栏展示
      "最近用的什么 model",不影响协议(每轮 body.model 仍然必传)
    """

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(primary_key=True)
    title: Mapped[str | None] = mapped_column(default=None)
    last_model: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class MessageRow(Base):
    """`messages` 表:0.4.0 按 Anthropic 协议 message 切行,对齐 Claude Code transcript。

    - `role`:`'user' | 'assistant'`(协议原生两种);tool_use 嵌在 assistant.content
      blocks,tool_result 嵌在 user.content blocks
    - `content`:JSON,原样存 anthropic content(字符串或 blocks 数组);
      `SELECT * ORDER BY seq` 直接构成 Anthropic messages 数组,零翻译成本
    - `seq`:会话内单调 0 起;`(conversation_id, seq)` UNIQUE
    - `model_name`:仅 role='assistant' 行非空,记本轮用的 entry name
    - 不设 FK:cascade delete 由 `ConversationRepo.delete()` 手动 DELETE FROM messages
      WHERE conversation_id = ?,行为不依赖 SQLite PRAGMA foreign_keys 全局开关
    """

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(index=True)
    seq: Mapped[int]
    role: Mapped[str]  # 'user' | 'assistant'
    content: Mapped[str]  # JSON-serialized;str 或 list[dict]
    model_name: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class ToolRow(Base):
    """`tools` 表:0.4.0 内置工具配置(4 条 seeded fixture)。

    0.4.0 不开放 CRUD —— 只允许改 `enabled` / `options`。`name` 即用户面 ID,
    也是 ToolRegistry 的 type key(0.4.0 name == type,预留同类多实例时再分)。
    全部 seeded 默认 `enabled=0`,用户必须显式打开。
    """

    __tablename__ = "tools"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(unique=True, index=True)
    type: Mapped[str]
    enabled: Mapped[int]  # 0/1;SQLite 无 BOOL 类型,统一用 int
    options: Mapped[str]  # JSON-serialized dict;migration v4 列默认 '{}'
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)

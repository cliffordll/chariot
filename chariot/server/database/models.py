"""SQLAlchemy 声明式 ORM 模型。

与 `migrations/*.sql` 字段对齐;SQL 是 schema 真源,ORM 镜像它。

v0 只有 `logs` 表(请求流水)。`upstreams` 表在 v3 migration 删除,架构上
server 自己就是 agent,不再有上游概念。

主键:`id` 是 32 字符 UUID4 hex,由 `default=` 在插入时生成。
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
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))

    __table_args__ = (Index("idx_logs_created_at", "created_at"),)

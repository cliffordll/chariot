"""SQLAlchemy 声明式 ORM 模型。

与 `migrations/*.sql` 字段对齐;SQL 是 schema 真源,ORM 镜像它。

- v1:`logs` 表(请求流水)
- v2(0.3.0):`models` 表 + `settings`(KV)—— 模型配置 DB 化
- v3(0.3.1):`models` 加 `params` 列;drop `settings` 表(active 概念删除,
  client 在 body.model 写 entry name 直接路由)

主键:
- `LogEntry.id` 是 32 字符 UUID4 hex(`default=` 插入时生成)
- `ModelRow.id` 是自增 int(name 才是用户面 ID,带 UNIQUE 约束)
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

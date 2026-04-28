"""admin /conversations 端点 —— 0.4.0 多轮会话管理。

端点
----
GET    /admin/conversations       → list({id, title, last_model, message_count, ts...})
                                    支持 limit / offset 分页
GET    /admin/conversations/{id}  → detail + 全部 messages
POST   /admin/conversations       → 显式创建(server 生成 ULID),返完整 entry
                                    非必需 —— `/v1/messages` 带未存在的 header 也 auto-create
                                    (模式 A vs 模式 B,见 DESIGN §3.1)
DELETE /admin/conversations/{id}  → cascade 删 messages
PATCH  /admin/conversations/{id}  → 改 title

错误码
------
- 400 `invalid_conversation_id` —— 显式 create 给的 id 非法 ULID(自动生成不会触发)
- 404 `conversation_not_found`  —— get / delete / patch 找不到 id
- 409 `conversation_id_exists`  —— 显式 create 给的 id 已用

ULID 生成:server 用 `python-ulid` 库,26 字符 base32 大写,单调时间戳前缀。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from ulid import ULID

from chariot.server.config import (
    ConfigError,
    ConversationNotFound,
    DuplicateConversationId,
)
from chariot.server.database.session import SessionDep
from chariot.server.repository.conversation_repo import (
    Conversation,
    ConversationRepo,
)
from chariot.server.service.exceptions import ServiceError

# 给 SDK 复用的 schema(沿用 controller/models.py 的"从 controller 导 schema"约定)
__all__ = [
    "ConversationDetailResponse",
    "ConversationOut",
    "ConversationsListResponse",
    "CreateConversationRequest",
    "MessageOut",
    "UpdateTitleRequest",
    "router",
]

router = APIRouter()

# ULID 严格 26 字符 base32 大写(跟 dataplane / DESIGN §3.1 一致)
_ULID_RE = re.compile(r"^[0-9A-Z]{26}$")


# ---------- Pydantic schema ----------


class ConversationOut(BaseModel):
    """单条 conversation 的对外形态(列表行 + 详情头都用)。"""

    id: str
    title: str | None
    last_model: str | None
    message_count: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_dataclass(cls, conv: Conversation) -> ConversationOut:
        return cls(
            id=conv.id,
            title=conv.title,
            last_model=conv.last_model,
            message_count=conv.message_count,
            created_at=conv.created_at,
            updated_at=conv.updated_at,
        )


class MessageOut(BaseModel):
    """单条 message 的对外形态(详情端点用,含 seq / model_name / 时间戳元数据)。"""

    seq: int
    role: str  # 'user' | 'assistant'
    content: Any  # str 或 anthropic content blocks 数组
    model_name: str | None
    created_at: datetime


class ConversationsListResponse(BaseModel):
    items: list[ConversationOut]
    limit: int
    offset: int


class ConversationDetailResponse(BaseModel):
    conversation: ConversationOut
    messages: list[MessageOut]


class CreateConversationRequest(BaseModel):
    """显式创建。`title` 可选;`id` 也可选 —— 不传 server 生成 ULID,
    传了则按 client 给的 id(必须合法 ULID)。"""

    title: str | None = None
    id: str | None = None


class UpdateTitleRequest(BaseModel):
    title: str | None  # 显式 None 也接受(把标题清空)


# ---------- 错误映射 ----------


def _to_service_error(exc: ConfigError) -> ServiceError:
    if isinstance(exc, DuplicateConversationId):
        return ServiceError(status=409, code="conversation_id_exists", message=str(exc))
    if isinstance(exc, ConversationNotFound):
        return ServiceError(status=404, code="conversation_not_found", message=str(exc))
    return ServiceError(status=400, code="bad_request", message=str(exc))


def _validate_ulid(conv_id: str) -> None:
    """非法 ULID → 400 invalid_conversation_id。"""
    if not _ULID_RE.fullmatch(conv_id):
        raise ServiceError(
            status=400,
            code="invalid_conversation_id",
            message=f"conversation id 必须是 26 字符 ULID,得到 {conv_id!r}",
        )


import json  # noqa: E402 -- 局部使用,避免文件顶部杂乱


def _message_to_out(row: Any) -> MessageOut:
    """ORM `MessageRow` → MessageOut(content 反序列化回原形态)。"""
    try:
        content: Any = json.loads(row.content)
    except (json.JSONDecodeError, TypeError):
        content = row.content
    return MessageOut(
        seq=row.seq,
        role=row.role,
        content=content,
        model_name=row.model_name,
        created_at=row.created_at,
    )


# ---------- 端点 ----------


@router.get("/conversations", response_model=ConversationsListResponse)
async def list_conversations(
    session: SessionDep,
    limit: int = 50,
    offset: int = 0,
) -> ConversationsListResponse:
    repo = ConversationRepo(session)
    convs = await repo.list_entries(limit=limit, offset=offset)
    return ConversationsListResponse(
        items=[ConversationOut.from_dataclass(c) for c in convs],
        limit=limit,
        offset=offset,
    )


@router.get("/conversations/{conv_id}", response_model=ConversationDetailResponse)
async def get_conversation(conv_id: str, session: SessionDep) -> ConversationDetailResponse:
    _validate_ulid(conv_id)
    repo = ConversationRepo(session)
    conv = await repo.get(conv_id)
    if conv is None:
        raise ServiceError(
            status=404,
            code="conversation_not_found",
            message=f"未知 conversation id: {conv_id!r}",
        )
    rows = await repo.list_messages(conv_id)
    return ConversationDetailResponse(
        conversation=ConversationOut.from_dataclass(conv),
        messages=[_message_to_out(r) for r in rows],
    )


@router.post(
    "/conversations",
    response_model=ConversationOut,
    status_code=201,
)
async def create_conversation(
    req: CreateConversationRequest,
    session: SessionDep,
) -> ConversationOut:
    """显式创建。`req.id` 不传 → server 生成 ULID;传了则严格校验形态。"""
    if req.id is None:
        conv_id = str(ULID())
    else:
        _validate_ulid(req.id)
        conv_id = req.id
    repo = ConversationRepo(session)
    try:
        conv = await repo.create(conv_id, title=req.title)
    except ConfigError as e:
        raise _to_service_error(e) from e
    return ConversationOut.from_dataclass(conv)


@router.delete("/conversations/{conv_id}", status_code=204)
async def delete_conversation(conv_id: str, session: SessionDep) -> None:
    _validate_ulid(conv_id)
    repo = ConversationRepo(session)
    try:
        await repo.delete(conv_id)
    except ConfigError as e:
        raise _to_service_error(e) from e


@router.patch("/conversations/{conv_id}", response_model=ConversationOut)
async def patch_conversation(
    conv_id: str,
    req: UpdateTitleRequest,
    session: SessionDep,
) -> ConversationOut:
    _validate_ulid(conv_id)
    repo = ConversationRepo(session)
    try:
        conv = await repo.update_title(conv_id, req.title)
    except ConfigError as e:
        raise _to_service_error(e) from e
    return ConversationOut.from_dataclass(conv)

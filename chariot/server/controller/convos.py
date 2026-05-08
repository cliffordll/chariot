"""admin /convos 端点 —— 0.4.0 多轮会话管理。

端点
----
GET    /admin/convos       → list({id, title, last_model, message_count, ts...})
                                    支持 limit / offset 分页
GET    /admin/convos/{id}  → detail + 全部 messages
POST   /admin/convos       → 显式创建(server 生成 ULID),返完整 entry
                                    非必需 —— `/v1/messages` 带未存在的 header 也 auto-create
                                    (模式 A vs 模式 B,见 DESIGN §3.1)
DELETE /admin/convos/{id}  → cascade 删 messages
PATCH  /admin/convos/{id}  → 改 title

错误码
------
- 400 `invalid_convo_id` —— 显式 create 给的 id 非法 ULID(自动生成不会触发)
- 404 `convo_not_found`  —— get / delete / patch 找不到 id
- 409 `convo_id_exists`  —— 显式 create 给的 id 已用

ULID 生成:server 用 `python-ulid` 库,26 字符 base32 大写,单调时间戳前缀。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from ulid import ULID

from chariot.agent.config import (
    ConfigError,
    ConvoNotFound,
    DuplicateConvoId,
)
from chariot.database.session import SessionDep
from chariot.repos.convo_repo import (
    Convo,
    ConvoRepo,
)
from chariot.server.service.exceptions import ServiceError

# 给 SDK 复用的 schema(沿用 controller/models.py 的"从 controller 导 schema"约定)
__all__ = [
    "ConvoDetailResponse",
    "ConvoOut",
    "ConvosListResponse",
    "CreateConvoRequest",
    "MessageOut",
    "UpdateTitleRequest",
    "router",
]

router = APIRouter()

# ULID 严格 26 字符 base32 大写(跟 dataplane / DESIGN §3.1 一致)
_ULID_RE = re.compile(r"^[0-9A-Z]{26}$")


# ---------- Pydantic schema ----------


class ConvoOut(BaseModel):
    """单条 conversation 的对外形态(列表行 + 详情头都用)。"""

    id: str
    title: str | None
    last_model: str | None
    message_count: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_dataclass(cls, conv: Convo) -> ConvoOut:
        return cls(
            id=conv.id,
            title=conv.title,
            last_model=conv.last_model,
            message_count=conv.message_count,
            created_at=conv.created_at,
            updated_at=conv.updated_at,
        )


class MessageOut(BaseModel):
    """单条 message 的对外形态(详情端点用,含 seq / provider_name / 时间戳元数据)。"""

    seq: int
    role: str  # 'user' | 'assistant'
    content: Any  # str 或 anthropic content blocks 数组
    provider_name: str | None
    created_at: datetime


class ConvosListResponse(BaseModel):
    items: list[ConvoOut]
    limit: int
    offset: int


class ConvoDetailResponse(BaseModel):
    convo: ConvoOut
    messages: list[MessageOut]


class CreateConvoRequest(BaseModel):
    """显式创建。`title` 可选;`id` 也可选 —— 不传 server 生成 ULID,
    传了则按 client 给的 id(必须合法 ULID)。"""

    title: str | None = None
    id: str | None = None


class UpdateTitleRequest(BaseModel):
    title: str | None  # 显式 None 也接受(把标题清空)


# ---------- 错误映射 ----------


def _to_service_error(exc: ConfigError) -> ServiceError:
    if isinstance(exc, DuplicateConvoId):
        return ServiceError(status=409, code="convo_id_exists", message=str(exc))
    if isinstance(exc, ConvoNotFound):
        return ServiceError(status=404, code="convo_not_found", message=str(exc))
    return ServiceError(status=400, code="bad_request", message=str(exc))


def _validate_ulid(convo_id: str) -> None:
    """非法 ULID → 400 invalid_convo_id。"""
    if not _ULID_RE.fullmatch(convo_id):
        raise ServiceError(
            status=400,
            code="invalid_convo_id",
            message=f"conversation id 必须是 26 字符 ULID,得到 {convo_id!r}",
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
        provider_name=row.provider_name,
        created_at=row.created_at,
    )


# ---------- 端点 ----------


@router.get("/convos", response_model=ConvosListResponse)
async def list_convos(
    session: SessionDep,
    limit: int = 50,
    offset: int = 0,
) -> ConvosListResponse:
    repo = ConvoRepo(session)
    convs = await repo.list_entries(limit=limit, offset=offset)
    return ConvosListResponse(
        items=[ConvoOut.from_dataclass(c) for c in convs],
        limit=limit,
        offset=offset,
    )


@router.get("/convos/{convo_id}", response_model=ConvoDetailResponse)
async def get_convo(convo_id: str, session: SessionDep) -> ConvoDetailResponse:
    _validate_ulid(convo_id)
    repo = ConvoRepo(session)
    conv = await repo.get(convo_id)
    if conv is None:
        raise ServiceError(
            status=404,
            code="convo_not_found",
            message=f"未知 conversation id: {convo_id!r}",
        )
    rows = await repo.list_messages(convo_id)
    return ConvoDetailResponse(
        convo=ConvoOut.from_dataclass(conv),
        messages=[_message_to_out(r) for r in rows],
    )


@router.post(
    "/convos",
    response_model=ConvoOut,
    status_code=201,
)
async def create_convo(
    req: CreateConvoRequest,
    session: SessionDep,
) -> ConvoOut:
    """显式创建。`req.id` 不传 → server 生成 ULID;传了则严格校验形态。"""
    if req.id is None:
        convo_id = str(ULID())
    else:
        _validate_ulid(req.id)
        convo_id = req.id
    repo = ConvoRepo(session)
    try:
        conv = await repo.create(convo_id, title=req.title)
    except ConfigError as e:
        raise _to_service_error(e) from e
    return ConvoOut.from_dataclass(conv)


@router.delete("/convos/{convo_id}", status_code=204)
async def delete_convo(convo_id: str, session: SessionDep) -> None:
    _validate_ulid(convo_id)
    repo = ConvoRepo(session)
    try:
        await repo.delete(convo_id)
    except ConfigError as e:
        raise _to_service_error(e) from e


@router.patch("/convos/{convo_id}", response_model=ConvoOut)
async def patch_convo(
    convo_id: str,
    req: UpdateTitleRequest,
    session: SessionDep,
) -> ConvoOut:
    _validate_ulid(convo_id)
    repo = ConvoRepo(session)
    try:
        conv = await repo.update_title(convo_id, req.title)
    except ConfigError as e:
        raise _to_service_error(e) from e
    return ConvoOut.from_dataclass(conv)

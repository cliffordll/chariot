"""admin /models 端点 —— GET 列表 / 探针 / entries CRUD。

0.3.0 起 model 配置住 DB(`models` 表),所有写操作走 ModelRepo 持久化 + 同步
Agent 状态。0.3.1 起 active 概念删除(client 在 body.model 写 entry name 路由),
对应的 POST /admin/models 切 active 端点也已删除。

端点
----
GET    /admin/models                          → entries 列表 + types
POST   /admin/models/{name}/probe             → 探针(临时 build,不副作用)

POST   /admin/models/entries                  → 创建 entry
PUT    /admin/models/entries/{name}           → 更新 type / options / params(立即 rebuild Model)
DELETE /admin/models/entries/{name}           → 删除
POST   /admin/models/entries/{name}/duplicate → 复制(碰撞自动 _copy_N)

错误码
------
- 400 `unknown_type`        —— type 不在 ModelRegistry.known_types()
- 404 `model_not_found`     —— update / delete / duplicate / probe 找不到 name
- 409 `name_exists`         —— create / duplicate 目标 name 冲突
- 502 `rebuild_failed`      —— update entry 后新参数 build Model 失败
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from chariot.server.agent import Agent
from chariot.server.config import (
    ChariotConfig,
    ConfigError,
    DuplicateModelName,
    ModelNotFound,
)
from chariot.server.database.session import SessionDep
from chariot.server.model.registry import ModelRegistry
from chariot.server.repository.model_repo import ModelRepo
from chariot.server.service.exceptions import ServiceError
from chariot.server.service.model_prober import ModelProber, ProbeError, ProbeResult

# 给 SDK 复用的 schema(SDK 沿用"从 controller 导 schema"约定)
__all__ = [
    "CreateEntryRequest",
    "DuplicateEntryRequest",
    "EntryResponse",
    "ModelsListResponse",
    "ProbeError",
    "ProbeResult",
    "UpdateEntryRequest",
    "router",
]

router = APIRouter()


# ---------- 共享 Pydantic 形态 ----------


class EntryResponse(BaseModel):
    """单条 model entry 的对外形态(name / type / options / params)。"""

    name: str
    type: str
    options: dict[str, Any]
    params: dict[str, Any]


# ---------- /admin/models GET ----------


class ModelsListResponse(BaseModel):
    """`GET /admin/models` 响应。

    `available` 是 entry name 列表(0.2.x 兼容);`entries` 是全量数组,
    `types` 是 ModelRegistry 已注册的 type key 列表。
    0.3.1 起删除 `active` 字段(active 概念退役)。
    """

    available: list[str]
    types: list[str]
    entries: list[EntryResponse]


@router.get("/models", response_model=ModelsListResponse)
async def list_models(session: SessionDep) -> ModelsListResponse:
    config = await ChariotConfig.from_db(session)
    entries = [
        EntryResponse(name=e.name, type=e.type, options=e.options, params=e.params)
        for e in config.models
    ]
    return ModelsListResponse(
        available=[e.name for e in config.models],
        types=ModelRegistry.known_types(),
        entries=entries,
    )


# ---------- /admin/models/{name}/probe POST ----------


@router.post("/models/{name}/probe", response_model=ProbeResult)
async def probe_model(name: str, session: SessionDep) -> ProbeResult:
    """探针:临时 build entry,发 1 条最小 messages 请求,报通不通 + 耗时。

    name 不存在 → 404(`model_not_found`)。其它任何失败(build 失败 / 上游 4xx /
    网络错)都不 raise,由 `ModelProber.probe` 包成 `ProbeResult(ok=False, error=...)`
    返回。
    """
    config = await ChariotConfig.from_db(session)
    for entry in config.models:
        if entry.name == name:
            return await ModelProber.probe(entry)
    raise ServiceError(
        status=404,
        code="model_not_found",
        message=f"未知 model name: {name!r}",
    )


# ---------- /admin/models/entries CRUD ----------


class CreateEntryRequest(BaseModel):
    name: str
    type: str
    options: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)


class UpdateEntryRequest(BaseModel):
    type: str | None = None
    options: dict[str, Any] | None = None
    params: dict[str, Any] | None = None


class DuplicateEntryRequest(BaseModel):
    """body 字段名是 `as`(SQL/CLI 习惯),Pydantic 用 alias 兼容 Python 关键字。"""

    model_config = ConfigDict(populate_by_name=True)

    as_name: str | None = Field(default=None, alias="as")


def _check_known_type(type_name: str) -> None:
    if type_name not in ModelRegistry.known_types():
        known = ", ".join(ModelRegistry.known_types()) or "(空)"
        raise ServiceError(
            status=400,
            code="unknown_type",
            message=f"未注册的 model type {type_name!r};已注册:{known}",
        )


async def _refresh_agent(session: SessionDep) -> None:
    """从 DB 重读 ChariotConfig + 全量重建 Agent。

    0.3.1 后任何 entry CRUD 都直接 rebuild,因为 active 概念删除了,
    每条 entry 都对应一个 Model 实例。如果某条 entry options 改了,
    新 options 立刻生效;build 失败 → `rebuild_failed` 502。
    """
    config = await ChariotConfig.from_db(session)
    try:
        Agent.install_from_config(config)
    except ConfigError as e:
        raise ServiceError(
            status=502,
            code="rebuild_failed",
            message=f"DB 改完后 rebuild Model 实例失败:{e}",
        ) from e


@router.post("/models/entries", response_model=EntryResponse, status_code=201)
async def create_entry(req: CreateEntryRequest, session: SessionDep) -> EntryResponse:
    _check_known_type(req.type)
    repo = ModelRepo(session)
    try:
        entry = await repo.create(
            name=req.name,
            type=req.type,
            options=req.options,
            params=req.params,
        )
    except DuplicateModelName as e:
        raise ServiceError(status=409, code="name_exists", message=str(e)) from e
    except ConfigError as e:
        raise ServiceError(status=400, code="bad_request", message=str(e)) from e
    await _refresh_agent(session)
    return EntryResponse(
        name=entry.name,
        type=entry.type,
        options=entry.options,
        params=entry.params,
    )


@router.put("/models/entries/{name}", response_model=EntryResponse)
async def update_entry(
    name: str,
    req: UpdateEntryRequest,
    session: SessionDep,
) -> EntryResponse:
    if req.type is not None:
        _check_known_type(req.type)
    repo = ModelRepo(session)
    try:
        entry = await repo.update(
            name,
            type=req.type,
            options=req.options,
            params=req.params,
        )
    except ModelNotFound as e:
        raise ServiceError(status=404, code="model_not_found", message=str(e)) from e
    except ConfigError as e:
        raise ServiceError(status=400, code="bad_request", message=str(e)) from e

    await _refresh_agent(session)
    return EntryResponse(
        name=entry.name,
        type=entry.type,
        options=entry.options,
        params=entry.params,
    )


@router.delete("/models/entries/{name}", status_code=204)
async def delete_entry(name: str, session: SessionDep) -> None:
    repo = ModelRepo(session)
    try:
        await repo.delete(name)
    except ModelNotFound as e:
        raise ServiceError(status=404, code="model_not_found", message=str(e)) from e
    await _refresh_agent(session)


@router.post(
    "/models/entries/{name}/duplicate",
    response_model=EntryResponse,
    status_code=201,
)
async def duplicate_entry(
    name: str,
    req: DuplicateEntryRequest,
    session: SessionDep,
) -> EntryResponse:
    repo = ModelRepo(session)
    try:
        entry = await repo.duplicate(name, as_name=req.as_name)
    except ModelNotFound as e:
        raise ServiceError(status=404, code="model_not_found", message=str(e)) from e
    except DuplicateModelName as e:
        raise ServiceError(status=409, code="name_exists", message=str(e)) from e
    except ConfigError as e:
        raise ServiceError(status=400, code="bad_request", message=str(e)) from e
    await _refresh_agent(session)
    return EntryResponse(
        name=entry.name,
        type=entry.type,
        options=entry.options,
        params=entry.params,
    )

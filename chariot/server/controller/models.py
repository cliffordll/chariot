"""admin /models 端点 —— GET 列表 / 切 active / 探针 / entries CRUD。

0.3.0 起 model 配置住 DB(`models` / `settings` 表),所有写操作走 ModelRepo
持久化 + 同步 Agent 状态。

端点
----
GET    /admin/models                          → 列表 + active + types
POST   /admin/models   {name}                 → 切换 active(写 settings + rebuild)
POST   /admin/models/{name}/probe             → 0.2.5 探针(临时 build,不副作用)

POST   /admin/models/entries                  → 0.3.0 创建 entry
PUT    /admin/models/entries/{name}           → 0.3.0 更新 type / options(active 自动 rebuild)
DELETE /admin/models/entries/{name}           → 0.3.0 删除(active 拒绝)
POST   /admin/models/entries/{name}/duplicate → 0.3.0 复制(碰撞自动 _copy_N)

错误码
------
- 400 `unknown_type`        —— type 不在 ModelRegistry.known_types()
- 400 `bad_model_name`      —— 切 active 时 name 不存在(沿用 0.2.x)
- 400 `cannot_delete_active`—— 删 active(让用户先 use 别的)
- 404 `model_not_found`     —— update / delete / duplicate / probe 找不到 name
- 409 `name_exists`         —— create / duplicate 目标 name 冲突
- 502 `rebuild_failed`      —— update active entry 后新参数 build Model 失败
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
    "SwitchModelRequest",
    "SwitchModelResponse",
    "UpdateEntryRequest",
    "router",
]

router = APIRouter()


# ---------- /admin/models GET ----------


class ModelsListResponse(BaseModel):
    available: list[str]
    active: str | None
    types: list[str]


@router.get("/models", response_model=ModelsListResponse)
async def list_models() -> ModelsListResponse:
    config = Agent.config()
    return ModelsListResponse(
        available=[e.name for e in config.models],
        active=Agent.active_name(),
        types=ModelRegistry.known_types(),
    )


# ---------- /admin/models POST(切 active)----------


class SwitchModelRequest(BaseModel):
    name: str


class SwitchModelResponse(BaseModel):
    active: str
    model: str  # 新生效 model 的 .name 标识(写入 logs.model)


@router.post("/models", response_model=SwitchModelResponse)
async def switch_model(req: SwitchModelRequest, session: SessionDep) -> SwitchModelResponse:
    """切 active model。0.3.0 起持久化到 settings.active_model + rebuild 内存实例。

    流程:
    1. `ModelRepo.set_active(name)` —— 校验 name 在 DB 并写 settings
    2. `ChariotConfig.from_db` + `Agent.refresh_config` —— 同步 Agent 缓存
       (确保 Agent._config 看到最新 entries)
    3. `Agent.switch_to(name)` —— rebuild + install Model 实例
    """
    repo = ModelRepo(session)
    try:
        await repo.set_active(req.name)
    except ModelNotFound as e:
        raise ServiceError(status=400, code="bad_model_name", message=str(e)) from e

    await _refresh_agent_config(session)
    try:
        agent = Agent.switch_to(req.name)
    except ConfigError as e:
        raise ServiceError(
            status=502,
            code="rebuild_failed",
            message=f"切到 {req.name!r} 但 build Model 失败:{e}",
        ) from e
    return SwitchModelResponse(active=req.name, model=agent.model.name)


# ---------- /admin/models/{name}/probe POST ----------


@router.post("/models/{name}/probe", response_model=ProbeResult)
async def probe_model(name: str) -> ProbeResult:
    """探针:临时 build entry,发 1 条最小 messages 请求,报通不通 + 耗时。

    name 不存在 → 404(`model_not_found`)。其它任何失败(build 失败 / 上游 4xx /
    网络错)都不 raise,由 `ModelProber.probe` 包成 `ProbeResult(ok=False, error=...)`
    返回。
    """
    config = Agent.config()
    for entry in config.models:
        if entry.name == name:
            return await ModelProber.probe(entry)
    raise ServiceError(
        status=404,
        code="model_not_found",
        message=f"未知 model name: {name!r}",
    )


# ---------- /admin/models/entries CRUD ----------


class EntryResponse(BaseModel):
    name: str
    type: str
    options: dict[str, Any]


class CreateEntryRequest(BaseModel):
    name: str
    type: str
    options: dict[str, Any] = Field(default_factory=dict)


class UpdateEntryRequest(BaseModel):
    type: str | None = None
    options: dict[str, Any] | None = None


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


async def _refresh_agent_config(session: SessionDep) -> None:
    """从 DB 重读 ChariotConfig,刷新 Agent._config 缓存(不动 active model 实例)。"""
    config = await ChariotConfig.from_db(session)
    Agent.refresh_config(config)


@router.post("/models/entries", response_model=EntryResponse, status_code=201)
async def create_entry(req: CreateEntryRequest, session: SessionDep) -> EntryResponse:
    _check_known_type(req.type)
    repo = ModelRepo(session)
    try:
        entry = await repo.create(name=req.name, type=req.type, options=req.options)
    except DuplicateModelName as e:
        raise ServiceError(status=409, code="name_exists", message=str(e)) from e
    except ConfigError as e:
        raise ServiceError(status=400, code="bad_request", message=str(e)) from e
    await _refresh_agent_config(session)
    return EntryResponse(name=entry.name, type=entry.type, options=entry.options)


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
        entry = await repo.update(name, type=req.type, options=req.options)
    except ModelNotFound as e:
        raise ServiceError(status=404, code="model_not_found", message=str(e)) from e
    except ConfigError as e:
        raise ServiceError(status=400, code="bad_request", message=str(e)) from e

    # 改了 active entry → 同步 rebuild active model 实例(新 options 即时生效)
    if Agent.active_name() == name:
        await _refresh_agent_config(session)
        try:
            Agent.switch_to(name)
        except ConfigError as e:
            raise ServiceError(
                status=502,
                code="rebuild_failed",
                message=f"active entry 改完后 rebuild 失败:{e}",
            ) from e
    else:
        await _refresh_agent_config(session)
    return EntryResponse(name=entry.name, type=entry.type, options=entry.options)


@router.delete("/models/entries/{name}", status_code=204)
async def delete_entry(name: str, session: SessionDep) -> None:
    if Agent.active_name() == name:
        raise ServiceError(
            status=400,
            code="cannot_delete_active",
            message=f"{name!r} 是 active model,先 use 别的 entry 再删",
        )
    repo = ModelRepo(session)
    try:
        await repo.delete(name)
    except ModelNotFound as e:
        raise ServiceError(status=404, code="model_not_found", message=str(e)) from e
    await _refresh_agent_config(session)


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
    await _refresh_agent_config(session)
    return EntryResponse(name=entry.name, type=entry.type, options=entry.options)

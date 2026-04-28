"""admin /tools 端点 —— 0.4.0 内置工具配置。

端点
----
GET  /admin/tools           → list 4 条 fixture(read_file / list_dir / shell_exec / http_get)
                              每条含 enabled / options / 该 tool 的 anthropic schema
PUT  /admin/tools/{name}    → 改 enabled / options;name 和 type 不允许改

设计:tools 表 0.4.0 不开放 CRUD —— 只支持改 enabled / options。详见
docs/DESIGN.md §12。

错误码
------
- 400 `bad_request`   —— options 不可 JSON 序列化等
- 404 `tool_not_found`—— PUT 给的 name 不在 4 条 fixture 里
- 502 `rebuild_failed`—— update 完 rebuild Tool 实例失败(新 options 校验不过)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from chariot.server.agent import Agent
from chariot.server.config import (
    ChariotConfig,
    ConfigError,
    ToolConfig,
    ToolEntry,
    ToolNotFound,
)
from chariot.server.database.session import SessionDep
from chariot.server.repository.tool_repo import ToolRepo
from chariot.server.service.exceptions import ServiceError
from chariot.server.tool.registry import ToolRegistry

__all__ = [
    "ToolOut",
    "ToolsListResponse",
    "UpdateToolRequest",
    "router",
]

router = APIRouter()


# ---------- Pydantic schema ----------


class ToolOut(BaseModel):
    """单条 tool entry 的对外形态。`schema` 是该 tool 的 anthropic tool definition
    (LLM 看到的形态),让 GUI / SDK 可预览。"""

    name: str
    type: str
    enabled: bool
    options: dict[str, Any]
    schema_: dict[str, Any] | None  # 别名:schema 是 BaseModel 保留字,Pydantic 会出 warning


class ToolsListResponse(BaseModel):
    types: list[str]  # ToolRegistry.known_types()
    entries: list[ToolOut]


class UpdateToolRequest(BaseModel):
    enabled: bool | None = None
    options: dict[str, Any] | None = None


# ---------- 错误映射 + helper ----------


def _to_service_error(exc: ConfigError) -> ServiceError:
    if isinstance(exc, ToolNotFound):
        return ServiceError(status=404, code="tool_not_found", message=str(exc))
    return ServiceError(status=400, code="bad_request", message=str(exc))


def _entry_to_out(entry: ToolEntry) -> ToolOut:
    """从 ToolEntry 构造 ToolOut + 顺手 build 一个 Tool 实例拿 schema。

    build 失败(options 不合法)→ schema_=None,但 entry 仍然返回(让 GUI 显示
    并让用户改 options)。
    """
    tool_schema: dict[str, Any] | None
    try:
        tool = ToolRegistry.build(entry)
        tool_schema = tool.schema()
    except ConfigError:
        tool_schema = None
    return ToolOut(
        name=entry.name,
        type=entry.type,
        enabled=entry.enabled,
        options=entry.options,
        schema_=tool_schema,
    )


async def _refresh_agent_tools(session: SessionDep) -> None:
    """从 DB 重读 ChariotConfig + ToolConfig 并全量重建 Agent。

    任何 tool update 后调,使新 options / enabled 立刻生效;build 失败 → 502。
    """
    model_config = await ChariotConfig.from_db(session)
    tool_config = await ToolConfig.from_db(session)
    try:
        Agent.install_from_config(model_config, tool_config)
    except ConfigError as e:
        raise ServiceError(
            status=502,
            code="rebuild_failed",
            message=f"DB 改完后 rebuild Tool 实例失败:{e}",
        ) from e


# ---------- 端点 ----------


@router.get("/tools", response_model=ToolsListResponse)
async def list_tools(session: SessionDep) -> ToolsListResponse:
    repo = ToolRepo(session)
    entries = await repo.list_entries()
    return ToolsListResponse(
        types=ToolRegistry.known_types(),
        entries=[_entry_to_out(e) for e in entries],
    )


@router.put("/tools/{name}", response_model=ToolOut)
async def update_tool(
    name: str,
    req: UpdateToolRequest,
    session: SessionDep,
) -> ToolOut:
    repo = ToolRepo(session)
    try:
        entry = await repo.update(name, enabled=req.enabled, options=req.options)
    except ConfigError as e:
        raise _to_service_error(e) from e
    await _refresh_agent_tools(session)
    return _entry_to_out(entry)

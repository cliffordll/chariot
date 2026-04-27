"""admin /models 端点 —— 列可用 model + 切换 active + 连通性探针。

- `GET /admin/models` → `{available, active, types}`
  - available:配置里的 model name 列表
  - active:当前激活的 name(MockModel fallback 时为 null)
  - types:ModelRegistry 已注册的 builder type 列表
- `POST /admin/models {name}` → 重建 model + Agent.install 覆盖 + 返回新 active
  - 失败(name 找不到 / build 出错)→ ConfigError 由全局 handler 转 HTTP
- `POST /admin/models/{name}/probe` → 临时 build + 发 1 条最小 messages 请求,
  返回 `{ok, latency_ms, error?}`。ok=false 时 error 透传上游错因(401 / 网络不通
  / 配置错 ...)。**真打上游一次,产生 ~1 token 费用**。

不动 config 文件。运行时切换是内存级覆盖。
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from chariot.server.agent import Agent
from chariot.server.config import ConfigError
from chariot.server.model.registry import ModelRegistry
from chariot.server.service.exceptions import ServiceError
from chariot.server.service.model_prober import ModelProber, ProbeError, ProbeResult

# 给 SDK 复用的 schema(SDK 沿用"从 controller 导 schema"约定)
__all__ = [
    "ModelsListResponse",
    "ProbeError",
    "ProbeResult",
    "SwitchModelRequest",
    "SwitchModelResponse",
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


# ---------- /admin/models POST ----------


class SwitchModelRequest(BaseModel):
    name: str


class SwitchModelResponse(BaseModel):
    active: str
    model: str  # 新生效 model 的 .name 标识(写入 logs.model)


@router.post("/models", response_model=SwitchModelResponse)
async def switch_model(req: SwitchModelRequest) -> SwitchModelResponse:
    try:
        agent = Agent.switch_to(req.name)
    except ConfigError as e:
        # 配置 / 注册层错误 → 400(用户输入错,如 name 未配 / type 未注册)
        raise ServiceError(status=400, code="bad_model_name", message=str(e)) from e
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

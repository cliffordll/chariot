"""Controller 层:所有 HTTP endpoint 汇总 + 异常映射。

两组 router 对外暴露,app.py 分别挂到不同 prefix:

- `admin_router`(挂在 `/admin`):管理面 —— runtime / logs / stats
- `dataplane_router`(无 prefix,端点内含 `/v1/*`):数据面 —— 仅 messages(0.2.0 起单端点)

分层约定:controller 负责 HTTP 协议(参数解析、状态码、错误映射);
business 逻辑在 `chariot.server.agent` + `chariot.server.model.*`;
错误工厂在 `chariot.server.controller.errors`。

`register_exception_handlers(app)` 注册 service 层 `ServiceError` → HTTP 响应的统一映射。
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from chariot.server.controller import dataplane, logs, models, runtime, stats
from chariot.server.controller.errors import chariot_error
from chariot.server.service.exceptions import ServiceError

admin_router = APIRouter()
admin_router.include_router(runtime.router)
admin_router.include_router(logs.router)
admin_router.include_router(stats.router)
admin_router.include_router(models.router)

dataplane_router = APIRouter()
dataplane_router.include_router(dataplane.router)


async def _handle_service_error(_request: Request, exc: Exception) -> JSONResponse:
    """`ServiceError` → chariot_error HTTP 响应(由 register_exception_handlers 注册)。

    签名参数用 `Exception` 对齐 Starlette 的 `ExceptionHandler` 协议;
    `add_exception_handler(ServiceError, ...)` 保证实参一定是 `ServiceError`。
    """
    assert isinstance(exc, ServiceError)
    return JSONResponse(
        status_code=exc.status,
        content=chariot_error(exc.code, exc.message, **exc.extra),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """把 service 层的 `ServiceError` 映射成统一的 chariot_error HTTP 响应。

    用显式 `add_exception_handler` 而非 `@app.exception_handler` 装饰器,
    避免 pyright 因装饰器副作用把函数判成 unused。
    """
    app.add_exception_handler(ServiceError, _handle_service_error)


__all__ = ["admin_router", "dataplane_router", "register_exception_handlers"]

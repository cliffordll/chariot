"""FastAPI app 工厂。"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from chariot import __version__
from chariot.server.agent import Agent
from chariot.server.config import ConfigLoader
from chariot.server.controller import (
    admin_router,
    dataplane_router,
    register_exception_handlers,
)
from chariot.server.database.session import dispose_db, init_db

_log = logging.getLogger("chariot.server.app")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """启动:init_db + 读 config + 装 agent;结束:卸 agent + dispose_db。

    `ConfigLoader.load()` 找不到文件返 `ChariotConfig.empty()`,Agent 走 MockModel
    fallback,行为兼容 0.1.0(零配置开箱可用)。配置 TOML 错或字段非法
    `ConfigError` 会上冒,server 不会起来 —— 强制配置正确性。
    """
    _log.info("starting chariot v%s (init db + load config + install agent)", __version__)
    await init_db()
    config = ConfigLoader.load()
    agent = Agent.install_from_config(config)
    _log.info("startup complete (agent.model=%s)", agent.model.name)
    try:
        yield
    finally:
        _log.info("shutdown: disposing db + resetting agent")
        Agent.uninstall()
        await dispose_db()
        _log.info("shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="chariot",
        version=__version__,
        description="chariot 本地智能体 server(admin + data plane)",
        lifespan=lifespan,
    )
    # Tauri webview 的 origin 是 `https://tauri.localhost`(Win)/ `tauri://localhost`(mac),
    # 与 server 的 `http://127.0.0.1:<port>` 跨 origin 会触发 preflight。server 只绑
    # localhost,外网打不到,allow_origins=["*"] 不引入攻击面。不用 credentials 所以 "*" 合法。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)
    app.include_router(admin_router, prefix="/admin")
    app.include_router(dataplane_router)
    return app

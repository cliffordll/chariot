"""FastAPI app 工厂。"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from chariot import __version__
from chariot.server.agent import Agent
from chariot.server.config import ChariotConfig
from chariot.server.controller import (
    admin_router,
    dataplane_router,
    register_exception_handlers,
)
from chariot.server.database.session import dispose_db, get_session_maker, init_db
from chariot.server.repository.model_repo import ModelRepo

_log = logging.getLogger("chariot.server.app")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """启动:init_db + seed mock(若空)+ 装 agent;结束:卸 agent + dispose_db。

    0.3.0 起源数据从 DB 装载(`ChariotConfig.from_db(session)`)。空表会被
    `ModelRepo.seed_if_empty()` 种入默认 mock entry,保证开箱可用。
    `ConfigError` 一致性破坏会上冒,server 不会起来。
    """
    _log.info("starting chariot v%s (init db + seed + install agent)", __version__)
    await init_db()

    sm = get_session_maker()
    if sm is None:
        raise RuntimeError("init_db 后 session_maker 仍为 None")
    async with sm() as session:
        await ModelRepo(session).seed_if_empty()
        config: ChariotConfig = await ChariotConfig.from_db(session)

    agent = Agent.install_from_config(config)
    _log.info("startup complete (entries=%d)", len(agent.models))
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

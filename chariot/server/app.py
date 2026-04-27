"""FastAPI app 工厂 + lifespan。

启动序列(`_startup`)
- init_db(目录 / engine / migrations / session_maker)
- seed mock entry(若 DB 空)
- 从 DB 装载 `ChariotConfig` → `Agent.install_from_config(config)`

关闭序列(`_shutdown`)
- `Agent.uninstall()`
- `dispose_db()`(释放连接池;SQLite WAL checkpoint 在最后一个连接关闭时触发)
"""

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
from chariot.server.database.session import dispose_db, init_db
from chariot.server.repository.model_repo import ModelRepo

_log = logging.getLogger("chariot.server.app")


async def _startup() -> Agent:
    """启动序列:init_db → seed mock(若空)→ 装 agent。返回当前 agent。

    `ConfigError`(配置一致性破坏)直接上冒,server 不会起来。
    """
    sm = await init_db()
    async with sm() as session:
        await ModelRepo(session).seed_if_empty()
        config = await ChariotConfig.from_db(session)
    return Agent.install_from_config(config)


async def _shutdown() -> None:
    """关闭序列:卸 agent + dispose db。"""
    Agent.uninstall()
    await dispose_db()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    _log.info("starting chariot v%s", __version__)
    agent = await _startup()
    _log.info("startup complete (entries=%d)", len(agent.models))
    try:
        yield
    finally:
        _log.info("shutdown begin")
        await _shutdown()
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

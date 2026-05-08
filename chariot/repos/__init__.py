"""Repository 层:封装 ORM 查询,endpoint / agent 按业务语义调用。

层次:
- `database/`:infra(engine / session / migrations / ORM 声明)
- `repos/`:data access(按表分类的 query helper)
- `controller/` / `service/` / `agent.py`:调用 repo,不直接写 SQLAlchemy

错误语义:repo **不抛** `HTTPException`。语义错(如 name 冲突 / 记录不存在)
抛 `ConfigError` 子类(`DuplicateProviderName / ProviderNotFound`),原始 SQLAlchemy
异常透传给调用方;controller 层用 `_to_service_error` 集中映射成 HTTP 错误。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from chariot.database.session import SessionDep
from chariot.repos.convo_repo import Convo, ConvoRepo
from chariot.repos.log_repo import LogRepo
from chariot.repos.tool_repo import ToolRepo


def _log_repo(session: SessionDep) -> LogRepo:
    return LogRepo(session)


def _convo_repo(session: SessionDep) -> ConvoRepo:
    return ConvoRepo(session)


def _tool_repo(session: SessionDep) -> ToolRepo:
    return ToolRepo(session)


LogRepoDep = Annotated[LogRepo, Depends(_log_repo)]
ConvoRepoDep = Annotated[ConvoRepo, Depends(_convo_repo)]
ToolRepoDep = Annotated[ToolRepo, Depends(_tool_repo)]

__all__ = [
    "Convo",
    "ConvoRepo",
    "ConvoRepoDep",
    "LogRepo",
    "LogRepoDep",
    "ToolRepo",
    "ToolRepoDep",
]

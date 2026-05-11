"""Chariot 配置数据形态 + 来源装载。

0.3.0 起源数据来源从 TOML 文件迁到 DB(`providers` 表;v6 之前叫 `models`),
由 `ProviderRepo` 持久化。
0.3.1 路由模型重构:active 概念删除,client 在 body.model 写 entry name 路由。
0.4.0 加 Tool 层:`ToolEntry` / `ToolConfig`(纯 enabled tools 容器)与
`ProviderEntry` / `ChariotConfig` 同结构。本模块汇总:

- `ProviderEntry` / `ChariotConfig`:Provider 配置(0.3.x;0.6.0 起 ModelEntry → ProviderEntry)
- `ToolEntry` / `ToolConfig`:tool 配置(0.4.0);`from_db(session)` 从 ToolRepo 装载

异常类型(`ConfigError` 及子类)0.6.0 起统一在 `chariot/agent/exceptions.py`;
本模块仅 re-export 维持向后兼容(`from chariot.agent.config import ConfigError`
旧 import 仍能拿到同一个类)。

历史:0.2.x 时这里有 `ConfigLoader`(读 `~/.chariot/config.toml`)+
`ChariotConfig.from_dict(raw)`(TOML dict 校验)。0.3.0 一并删除,见
`docs/history/0.2.6/DESIGN.md` §6。

模块级零自由函数 / 零可变变量。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

# 异常类型 0.6.0 起 re-export 自 exceptions.py,本模块不重复定义
from chariot.agent.exceptions import (
    ConfigError,
    ConversationNotFound,
    DuplicateConversationId,
    DuplicateProviderName,
    ProviderNotFound,
    ToolNotFound,
)

# ProviderEntry 0.7.2 起迁到 chariot.models.provider;本模块保留 re-export 维持
# 现有 `from chariot.agent.config import ProviderEntry` 调用方继续工作。
from chariot.models.provider import ProviderEntry

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


__all__ = [
    "ChariotConfig",
    "ConfigError",
    "ConversationNotFound",
    "DuplicateConversationId",
    "DuplicateProviderName",
    "ProviderEntry",
    "ProviderNotFound",
    "ToolConfig",
    "ToolEntry",
    "ToolNotFound",
]


@dataclass(frozen=True)
class ChariotConfig:
    """顶层配置:providers 列表。

    0.3.1 删除 `active` 字段(client 用 body.model 路由,server 不持有 active 状态)。
    0.6.0 字段 `models` rename → `providers`(跟 v6 表名一致)。
    `from_db(session)` 是唯一外部装载入口。
    """

    providers: tuple[ProviderEntry, ...] = ()

    # ---- 构造工厂 ----

    @classmethod
    def empty(cls) -> ChariotConfig:
        return cls()

    @classmethod
    async def from_db(cls, session: AsyncSession) -> ChariotConfig:
        """从 DB(`providers` 表)装载完整配置。表空 → `empty()`。"""
        from chariot.repos.provider_repo import ProviderRepo  # 避免循环 import

        repo = ProviderRepo(session)
        entries = tuple(await repo.list_entries())
        return cls(providers=entries)

    # ---- 查询 ----

    def is_empty(self) -> bool:
        return not self.providers

    def find_entry(self, name: str) -> ProviderEntry | None:
        for entry in self.providers:
            if entry.name == name:
                return entry
        return None


@dataclass(frozen=True)
class ToolEntry:
    """单条 tool 配置(0.4.0)。

    `name` == ToolRegistry type key(0.4.0 一种 type 一个 entry,预留同类多实例
    时再分;详见 `docs/DESIGN.md` §8)。`options` 形态依 type 而定:

    - `read_file`:`{"max_bytes": int}`
    - `list_dir`:`{}`
    - `shell_exec`:`{"workdir": str, "timeout_s": int}`
    - `http_get`:`{"allowed_domains": list[str], "max_bytes": int}`
    """

    name: str
    type: str
    enabled: bool
    options: dict[str, Any]


@dataclass(frozen=True)
class ToolConfig:
    """顶层 tool 配置:enabled tools 列表(0.4.0)。

    Agent 在 lifespan startup 调 `from_db(session)` → `ToolRepo.list_enabled()`
    装载后,build 出 Tool 实例字典 `{name → Tool}`。disabled 的 entry 不进容器,
    Agent 看不到。
    """

    tools: tuple[ToolEntry, ...] = ()

    @classmethod
    def empty(cls) -> ToolConfig:
        return cls()

    @classmethod
    async def from_db(cls, session: AsyncSession) -> ToolConfig:
        """从 DB(`tools` 表)装载 enabled entries。表空(seed 未跑)/ 全 disabled → `empty()`。"""
        from chariot.repos.tool_repo import ToolRepo  # 避免循环 import

        repo = ToolRepo(session)
        entries = tuple(await repo.list_enabled())
        return cls(tools=entries)

    def is_empty(self) -> bool:
        return not self.tools

    def find_entry(self, name: str) -> ToolEntry | None:
        for entry in self.tools:
            if entry.name == name:
                return entry
        return None

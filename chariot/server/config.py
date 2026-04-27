"""Chariot 配置数据形态 + 来源装载。

0.3.0 起源数据来源从 TOML 文件迁到 DB(`models` / `settings` 表),由
`ModelRepo` 持久化。本模块只保留:

- `ConfigError`:配置层错误(startup 期 raise,不是 HTTP)
- `ModelEntry`:单条 model 配置(name / type / options)—— 数据形态
- `ChariotConfig`:顶层(models 列表 + active)—— 数据形态;`from_db(session)`
  classmethod 从 ModelRepo 装载

历史:0.2.x 时这里有 `ConfigLoader`(读 `~/.chariot/config.toml`)+
`ChariotConfig.from_dict(raw)`(TOML dict 校验)。0.3.0 一并删除,见
`docs/history/0.2.6/DESIGN.md` §6。

模块级零自由函数 / 零可变变量。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class ConfigError(Exception):
    """配置层错误;startup 时 raise(不是 HTTP 错误)。"""


@dataclass(frozen=True)
class ModelEntry:
    """单条 model 配置条目(数据形态)。

    `options` 显式必填(无默认),调用方传 `{}` 表示"无选项"。和 0.2.x 一致。
    """

    name: str  # 用户面名称(`chariot model list` 列出来)
    type: str  # builder 类型 key(mock / anthropic / llama_local 等)
    options: dict[str, Any]


@dataclass(frozen=True)
class ChariotConfig:
    """顶层配置:models 列表 + active 名称。

    `from_db(session)` 是唯一外部装载入口。0.3.0 删除了 TOML 装载路径
    (`from_dict` / `ConfigLoader`),保留数据形态供 ModelRegistry.build /
    Agent.install_from_config 复用。
    """

    models: tuple[ModelEntry, ...] = ()
    active: str | None = None

    # ---- 构造工厂 ----

    @classmethod
    def empty(cls) -> ChariotConfig:
        return cls()

    @classmethod
    async def from_db(cls, session: AsyncSession) -> ChariotConfig:
        """从 DB(`models` 表 + `settings.active_model`)装载完整配置。

        - active 指向不存在的 entry → `ConfigError`(数据不一致,需修复)
        - 表空 → `empty()`(lifespan 调用方应当先 `seed_if_empty()`)
        """
        from chariot.server.repository.model_repo import ModelRepo  # 避免循环 import

        repo = ModelRepo(session)
        entries = tuple(await repo.list_entries())
        active = await repo.get_active()
        if active is not None and not any(e.name == active for e in entries):
            raise ConfigError(f"settings.active_model={active!r} 在 models 表里找不到")
        return cls(models=entries, active=active)

    # ---- 查询 ----

    def is_empty(self) -> bool:
        return not self.models and self.active is None

    def active_entry(self) -> ModelEntry | None:
        """按 active 名称返对应 entry;active=None 返 None。

        active 指向未知 name 视为一致性破坏,raise `ConfigError`(`from_db` 已校验,
        理论上不该到这;手工构造的 ChariotConfig 才可能踩到)。
        """
        if self.active is None:
            return None
        for entry in self.models:
            if entry.name == self.active:
                return entry
        raise ConfigError(f"active 指向未知 model name: {self.active!r}")

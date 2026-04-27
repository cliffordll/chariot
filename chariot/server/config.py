"""Chariot 配置数据形态 + 来源装载。

0.3.0 起源数据来源从 TOML 文件迁到 DB(`models` 表),由 `ModelRepo` 持久化。
0.3.1 路由模型重构:active 概念删除,client 在 body.model 写 entry name 路由。
本模块只保留:

- `ConfigError`:配置层错误(startup 期 raise,不是 HTTP)
- `ModelEntry`:单条 model 配置(name / type / options / params)—— 数据形态
- `ChariotConfig`:顶层(纯 entries 容器)—— 数据形态;`from_db(session)` classmethod
  从 ModelRepo 装载

历史:0.2.x 时这里有 `ConfigLoader`(读 `~/.chariot/config.toml`)+
`ChariotConfig.from_dict(raw)`(TOML dict 校验)。0.3.0 一并删除,见
`docs/history/0.2.6/DESIGN.md` §6。

模块级零自由函数 / 零可变变量。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _empty_params() -> dict[str, Any]:
    """frozen dataclass 默认值工厂;内联 lambda pyright 推断不出 dict[str, Any]。"""
    return {}


class ConfigError(Exception):
    """配置层错误;startup 时 raise(不是 HTTP 错误)。

    子类用于让 controller 精确映射 HTTP 状态码,不靠 message 文本子串判断。
    """


class ModelNotFound(ConfigError):  # noqa: N818 — 短名对调用方更友好,语义明显是异常
    """指定 name 在 DB 里找不到(update / delete / duplicate src)。

    Controller 转 HTTP 404。
    """


class DuplicateModelName(ConfigError):  # noqa: N818 — 同上
    """name 已存在(create / duplicate 目标名冲突)。

    Controller 转 HTTP 409。
    """


@dataclass(frozen=True)
class ModelEntry:
    """单条 model 配置条目(数据形态)。

    - `options`:build Model 实例所需(model / api_key / base_url ...)
    - `params`:0.3.1 加。runtime sampling 默认值(temperature / top_p / max_tokens),
      给前端发请求时填默认 body 字段用。**server 不主动注入 body**,只通过 API
      暴露给 client。
    """

    name: str  # 用户面名称(`chariot model list` 列出来 / client 在 body.model 写)
    type: str  # builder 类型 key(mock / anthropic / llama_local 等)
    options: dict[str, Any]
    params: dict[str, Any] = field(default_factory=_empty_params)


@dataclass(frozen=True)
class ChariotConfig:
    """顶层配置:models 列表。

    0.3.1 删除 `active` 字段(client 用 body.model 路由,server 不持有 active 状态)。
    `from_db(session)` 是唯一外部装载入口。
    """

    models: tuple[ModelEntry, ...] = ()

    # ---- 构造工厂 ----

    @classmethod
    def empty(cls) -> ChariotConfig:
        return cls()

    @classmethod
    async def from_db(cls, session: AsyncSession) -> ChariotConfig:
        """从 DB(`models` 表)装载完整配置。表空 → `empty()`。"""
        from chariot.server.repository.model_repo import ModelRepo  # 避免循环 import

        repo = ModelRepo(session)
        entries = tuple(await repo.list_entries())
        return cls(models=entries)

    # ---- 查询 ----

    def is_empty(self) -> bool:
        return not self.models

    def find_entry(self, name: str) -> ModelEntry | None:
        for entry in self.models:
            if entry.name == name:
                return entry
        return None

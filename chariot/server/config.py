"""Chariot 配置加载 —— `~/.chariot/config.toml` 解析为 `ChariotConfig`。

0.2.0 起 chariot 通过配置文件指定真实模型;无配置时 lifespan 走 MockModel
fallback,不影响开发体验。

类设计
------
- `ModelEntry` (frozen):单条 model 配置(name / type / options)
- `ChariotConfig` (frozen):顶层 — models 列表 + active 名称
  - `empty()` classmethod:空配置(active=None,models=())
  - `active_entry()`:按 active 字段在 models 里找;active=None 返 None
  - `from_dict(raw)`:TOML 解析后的 dict → `ChariotConfig`,做完整性校验
- `ConfigLoader`:文件路径解析 + 读取
  - `DEFAULT_PATH` = `~/.chariot/config.toml`
  - 环境变量 `CHARIOT_CONFIG` 覆盖默认路径
  - `load(path=None)`:读文件 / fallback `empty()`
- `ConfigError`:配置层错误(startup 期 raise,不是 HTTP)

模块级零自由函数 / 零可变变量。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, cast


class ConfigError(Exception):
    """配置加载 / 解析期错误;startup 时 raise(不是 HTTP 错误)。"""


@dataclass(frozen=True)
class ModelEntry:
    """单条 model 配置条目。

    `options` 是必填字段(无默认),调用方显式传 `{}` 表示"无选项"。这样契约
    清晰、避免 default_factory 引入模块级 helper,与项目"封装内聚优先"基调一致。
    """

    name: str  # 用户面名称(`chariot model list` 列出来)
    type: str  # builder 类型 key(mock / anthropic / llama_local 等)
    options: dict[str, Any]

    @classmethod
    def from_dict(cls, raw: Any, *, index: int) -> ModelEntry:
        """解析一条 `[[models]]` table → `ModelEntry`,做字段级校验。

        index 只用来给错误信息定位是哪一条出问题。entry 间的关系(name 唯一性、
        active 引用)由 `ChariotConfig.from_dict` 负责,不在本方法。
        """
        if not isinstance(raw, dict):
            raise ConfigError(f"models[{index}] 必须是 table")
        md = cast(dict[str, Any], raw)

        name = md.get("name")
        if not isinstance(name, str) or not name:
            raise ConfigError(f"models[{index}].name 必须是非空字符串")

        type_ = md.get("type")
        if not isinstance(type_, str) or not type_:
            raise ConfigError(f"models[{index}].type 必须是非空字符串(model name={name!r})")

        options = md.get("options", {})
        if not isinstance(options, dict):
            raise ConfigError(f"models[{index}].options 必须是 table(model name={name!r})")

        return cls(name=name, type=type_, options=cast(dict[str, Any], options))


@dataclass(frozen=True)
class ChariotConfig:
    """顶层配置:models 列表 + active 名称。"""

    models: tuple[ModelEntry, ...] = ()
    active: str | None = None

    # ---- 构造工厂 ----

    @classmethod
    def empty(cls) -> ChariotConfig:
        return cls()

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ChariotConfig:
        """从 TOML 解析后的 dict 构造 + 校验完整性。

        - `models` 必须是数组,字段级校验 delegate 给 `ModelEntry.from_dict`
        - `name` 在所有条目里必须唯一(本类负责的关系约束)
        - `active.model` 若给了,必须能在 `models[*].name` 里找到
        """
        models_raw = raw.get("models", [])
        if not isinstance(models_raw, list):
            raise ConfigError("配置里的 'models' 必须是数组(`[[models]]`)")

        entries: list[ModelEntry] = []
        names_seen: set[str] = set()
        for i, m in enumerate(cast(list[Any], models_raw)):
            entry = ModelEntry.from_dict(m, index=i)
            if entry.name in names_seen:
                raise ConfigError(f"重复的 model name: {entry.name!r}")
            names_seen.add(entry.name)
            entries.append(entry)

        active = cls._parse_active(raw, entries)
        return cls(models=tuple(entries), active=active)

    # ---- 查询 ----

    def is_empty(self) -> bool:
        return not self.models and self.active is None

    def active_entry(self) -> ModelEntry | None:
        """按 active 名称返对应 entry;active=None 返 None。

        active 指向未知 name 在 `from_dict` 已经拦截过;此处到达视为一致性破坏,
        给个明确报错而不是默默 None。
        """
        if self.active is None:
            return None
        for entry in self.models:
            if entry.name == self.active:
                return entry
        raise ConfigError(f"active 指向未知 model name: {self.active!r}")

    # ---- 解析辅助(私有 staticmethod) ----

    @staticmethod
    def _parse_active(raw: dict[str, Any], entries: list[ModelEntry]) -> str | None:
        active_section = raw.get("active")
        if active_section is None:
            return None
        if not isinstance(active_section, dict):
            raise ConfigError("配置里的 'active' 必须是 table(`[active]`)")
        active_name = cast(dict[str, Any], active_section).get("model")
        if active_name is None:
            return None
        if not isinstance(active_name, str) or not active_name:
            raise ConfigError("active.model 必须是非空字符串")
        if not any(e.name == active_name for e in entries):
            raise ConfigError(f"active.model={active_name!r} 在 models 列表里找不到")
        return active_name


class ConfigLoader:
    """文件路径解析 + TOML 读取。"""

    DEFAULT_PATH: ClassVar[Path] = Path.home() / ".chariot" / "config.toml"
    ENV_OVERRIDE: ClassVar[str] = "CHARIOT_CONFIG"

    @classmethod
    def load(cls, path: Path | None = None) -> ChariotConfig:
        """读取并解析配置文件。

        优先级:`path` 参数 > 环境变量 `CHARIOT_CONFIG` > 默认 `~/.chariot/config.toml`。
        文件不存在 → `ChariotConfig.empty()`(由 lifespan 走 MockModel)。
        TOML 语法错误 / 内容校验失败 → `ConfigError`。
        """
        target = cls._resolve_path(path)
        if not target.exists():
            return ChariotConfig.empty()
        try:
            raw_bytes = target.read_bytes()
        except OSError as e:
            raise ConfigError(f"读取 {target} 失败: {e}") from e
        try:
            raw = tomllib.loads(raw_bytes.decode("utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
            raise ConfigError(f"{target} TOML 解析失败: {e}") from e
        return ChariotConfig.from_dict(raw)

    @classmethod
    def _resolve_path(cls, path: Path | None) -> Path:
        if path is not None:
            return path
        env_val = os.environ.get(cls.ENV_OVERRIDE)
        if env_val:
            return Path(env_val)
        return cls.DEFAULT_PATH

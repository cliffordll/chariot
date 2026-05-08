"""`ModelRegistry` —— 按 type 名字派发 `Model` 构造。

把"type 字符串(配置里的 `[[models]] type = ...`)→ 对应 Model 类的 from_config"
关系收在一个类里。`Agent.install_from_config` 通过 `ModelRegistry.build(entry)`
得到具体 Model 实例,完全不感知"有哪些后端"。

使用
----
1. 在 model 类上实现 `from_config(options) -> Self` classmethod
2. 在 `chariot/server/model/__init__.py` 显式注册:

   ```python
   ModelRegistry.register("mock", MockModel)
   ModelRegistry.register("anthropic", AnthropicModel)
   ```

3. 调用方:`model = ModelRegistry.build(entry)`(`entry.type` 派发到对应 builder)

加新后端 = 实现一个 model 类 + 在 `__init__.py` 加一行 `register(...)`,不动
Agent / Controller / lifespan。

模块级零自由函数 / 零可变变量,所有状态挂在 `ModelRegistry` ClassVar 上。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

from chariot.agent.config import ConfigError, ProviderEntry
from chariot.server.model.base import Model

# 每个 builder 是一个 callable:options dict → Model 实例
ModelBuilder = Callable[[dict[str, Any]], Model]


class ModelRegistry:
    """type_name → `Model.from_config` 的注册中心(类级单例)。

    类承担三件事:
    1. `register(type_name, model_cls)`:把 `model_cls.from_config` 收进 `_builders`
    2. `build(entry)`:按 `entry.type` 派发到 builder,调用它构造 Model
    3. `known_types()`:枚举所有已注册 type 名

    模块级不暴露任何状态;`_builders` ClassVar 是唯一注册表。

    测试隔离工具(snapshot/restore)收在 `tests/server/conftest.py` 的 fixture 里,
    生产 API 不暴露这些。
    """

    _builders: ClassVar[dict[str, ModelBuilder]] = {}

    # ---- 注册 ----

    @classmethod
    def register(cls, type_name: str, model_cls: type[Model]) -> None:
        """显式注册一个 model 类。

        - type 重复 → `ValueError`
        - `model_cls` 静态保证是 `type[Model]`(ABC 子类),`from_config` 必有
        - 调用时机:`chariot/server/model/__init__.py` 模块加载阶段集中调用
        """
        if type_name in cls._builders:
            raise ValueError(f"重复注册 model type: {type_name!r}")
        cls._builders[type_name] = model_cls.from_config

    # ---- 构造 ----

    @classmethod
    def build(cls, entry: ProviderEntry) -> Model:
        """按 `entry.type` 派发到对应 `from_config(entry.options)`。

        type 未注册 → `ConfigError`(不是 HTTP 错误,startup 期 raise)。
        """
        try:
            builder = cls._builders[entry.type]
        except KeyError as e:
            known = ", ".join(cls.known_types()) or "(空)"
            raise ConfigError(
                f"未知 model type: {entry.type!r} (model name={entry.name!r});已注册:{known}"
            ) from e
        return builder(entry.options)

    @classmethod
    def known_types(cls) -> list[str]:
        return sorted(cls._builders)

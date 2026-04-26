"""`ModelRegistry` —— 按 type 名字派发 `Model` 构造。

把"type 字符串(配置里的 `[[models]] type = ...`)→ 对应 Model 类的 from_config"
关系收在一个类里。Agent.install_from_config 通过 `ModelRegistry.build(entry)`
得到具体 Model 实例,完全不感知"有哪些后端"。

使用
----
注册:
```
@ModelRegistry.register("mock")
class MockModel:
    @classmethod
    def from_config(cls, options: dict[str, Any]) -> MockModel: ...
```

构造:
```
entry = ModelEntry(name="x", type="mock", options={})
model = ModelRegistry.build(entry)
```

加新后端 = 写个新文件 + `@ModelRegistry.register("xxx")` 装饰一行,不动 Agent /
Controller / lifespan。

模块级零自由函数 / 零可变变量,所有状态挂在 `ModelRegistry` ClassVar 上。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar, cast

from chariot.server.config import ConfigError, ModelEntry
from chariot.server.model.base import Model

# 每个 builder 是一个 callable:options dict → Model 实例
ModelBuilder = Callable[[dict[str, Any]], Model]


class ModelRegistry:
    """type_name → `Model.from_config` 的注册中心(类级单例)。

    类承担四件事:
    1. `register(type_name)`:装饰器,把 `cls.from_config` 收进 `_builders`
    2. `build(entry)`:按 `entry.type` 派发到 builder,调用它构造 Model
    3. `known_types()`:枚举所有已注册 type 名
    4. `snapshot / restore`:测试隔离用(避免一个用例污染另一个)

    模块级不暴露任何状态;`_builders` ClassVar 是唯一注册表。
    """

    _builders: ClassVar[dict[str, ModelBuilder]] = {}

    # ---- 注册 ----

    @classmethod
    def register(cls, type_name: str) -> Callable[[type[Model]], type[Model]]:
        """装饰器:`@ModelRegistry.register("xxx")` 收集 model 类。

        被装饰类必须实现 `from_config(options) -> Self` classmethod;否则在 import
        阶段就 raise(配置错误尽量早暴露)。
        """

        def decorator(model_cls: type[Model]) -> type[Model]:
            if type_name in cls._builders:
                raise ValueError(f"重复注册 model type: {type_name!r}")
            builder = getattr(model_cls, "from_config", None)
            if not callable(builder):
                raise TypeError(
                    f"{model_cls.__name__} 注册为 model type {type_name!r} 但缺少 "
                    "from_config classmethod"
                )
            # getattr 给的类型是 object;运行期已校验 callable + Model 协议,
            # 这里 cast 表达"我们信任这层契约"
            cls._builders[type_name] = cast(ModelBuilder, builder)
            return model_cls

        return decorator

    # ---- 构造 ----

    @classmethod
    def build(cls, entry: ModelEntry) -> Model:
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

    # ---- 测试隔离 ----

    @classmethod
    def snapshot(cls) -> dict[str, ModelBuilder]:
        """返回当前注册表的浅拷贝;给测试 setup/teardown 用。"""
        return dict(cls._builders)

    @classmethod
    def restore(cls, snap: dict[str, ModelBuilder]) -> None:
        """把注册表恢复成 `snap` 的内容;给测试 teardown 用。"""
        cls._builders.clear()
        cls._builders.update(snap)

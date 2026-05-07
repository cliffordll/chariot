"""Provider 注册表(0.6.0+)。

替代 0.5.0 `chariot/server/model/registry.py` 的 `ModelRegistry`。

注册表是**类承载的单例**(ClassVar 字典挂在类上),不是模块级可变变量
(CLAUDE.md ⭐ 规则 2)。`register` / `build` / `known_types` 都是
classmethod。

`build(type_name, options)` 接两个原始参数,**不依赖 `ModelEntry`** —— 让
`chariot/providers/` 不反向 import `chariot/server/config.py`(过渡期保留
解耦)/ 0.6.0 S.4 后的 `chariot/agent/config.py`。AIAgent 装载时拆 ModelEntry:
`registry.build(entry.type, entry.options)`。
"""

from __future__ import annotations

from typing import Any, ClassVar

from chariot.agent.exceptions import ConfigError
from chariot.providers.base import BaseProvider


class ProviderRegistry:
    """`type_name → BaseProvider 子类` 注册表。

    使用方式:

        # 子类定义文件末尾 / __init__.py 注册
        ProviderRegistry.register("anthropic", AnthropicProvider)

        # AIAgent.from_db 装载时构造实例
        provider = ProviderRegistry.build("anthropic", entry.options)
    """

    _registry: ClassVar[dict[str, type[BaseProvider]]] = {}

    @classmethod
    def register(cls, type_name: str, provider_cls: type[BaseProvider]) -> None:
        """注册一种 Provider 类型。

        重复注册同 `type_name` 抛 `ValueError`(避免 import 顺序差异导致的
        覆盖错误)。如果要替换,先 `unregister` 再 `register`。
        """
        if type_name in cls._registry:
            raise ValueError(f"duplicate provider type: {type_name}")
        cls._registry[type_name] = provider_cls

    @classmethod
    def unregister(cls, type_name: str) -> None:
        """撤销注册(测试用 / 0.11.0+ Plugins 系统热卸载用)。

        type_name 不存在不报错(idempotent)。
        """
        cls._registry.pop(type_name, None)

    @classmethod
    def build(cls, type_name: str, options: dict[str, Any]) -> BaseProvider:
        """根据 type_name 找 Provider 类,调 `from_options(options)` 构造实例。

        type_name 未注册抛 `ConfigError`(让上层 AIAgent.from_db 报"models 表
        含未知 provider type")。options 不合法由 `from_options` 自己抛
        ConfigError 子类。
        """
        provider_cls = cls._registry.get(type_name)
        if provider_cls is None:
            known = sorted(cls._registry.keys())
            raise ConfigError(f"unknown provider type: {type_name!r}; known types: {known}")
        return provider_cls.from_options(options)

    @classmethod
    def known_types(cls) -> set[str]:
        """已注册的 type_name 集合(给 CLI `chariot model add --help` / 文档用)。"""
        return set(cls._registry.keys())

    @classmethod
    def _reset(cls) -> None:
        """清空注册表(**仅测试 fixture 用**;业务代码不应调)。"""
        cls._registry.clear()

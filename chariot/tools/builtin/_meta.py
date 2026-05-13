"""builtin 工具元数据 + `@builtin_tool` 装饰器。

目标：以后新增内置工具只需写一个文件、加一个装饰器。

用法
----
在 `chariot/tools/builtin/<name>.py` 中：

    from chariot.tools.builtin._meta import builtin_tool

    @builtin_tool(defaults={"max_bytes": 1048576})
    class ReadFileTool(BaseTool):
        ...

装饰器自动完成：
1. 从类名推断 tool name（`ReadFileTool` → `read_file`）
2. 将类注册到 `ToolRegistry`
3. 把 seed fixture（name、type、默认 options）收集到 `_BUILTIN_SEEDS`

扫描
----
`BuiltinToolMeta.ensure_discovered()` 遍历 `builtin/` 下所有非下划线开头的 .py
文件并 import，触发上述副作用。调用方在需要 seed_entries 前显式调用一次即可。
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from typing import Any

from chariot.tools.base import BaseTool
from chariot.tools.registry import ToolRegistry

# 收集所有 @builtin_tool 标记的 seed fixture
_BUILTIN_SEEDS: list[tuple[str, str, dict[str, Any]]] = []
_DISCOVERED = False


def _class_name_to_snake(name: str) -> str:
    """`WriteFileTool` → `write_file`。

    规则：去掉末尾 "Tool"，在大小写切换处插下划线，全小写。
    """
    if name.endswith("Tool"):
        name = name[:-4]
    # 插入下划线：小写+大写、大写+大写+小写
    s1 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    s2 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s1)
    return s2.lower()


def builtin_tool(defaults: dict[str, Any] | None = None) -> Any:
    """装饰器：把 `BaseTool` 子类注册为 builtin 工具。

    :param defaults: seed fixture 的默认 options 字典;工具无 options 时传 `{}` 或省略。
    """

    def decorator(cls: Any) -> Any:
        if not isinstance(cls, type) or not issubclass(cls, BaseTool):
            raise TypeError("@builtin_tool 只能装饰 BaseTool 子类")

        tool_name = _class_name_to_snake(cls.__name__)
        opts = defaults if defaults is not None else {}

        # 1. 注册到 Registry
        ToolRegistry.register(tool_name, cls)

        # 2. 收集 seed fixture
        _BUILTIN_SEEDS.append((tool_name, tool_name, opts))

        return cls

    return decorator


class BuiltinToolMeta:
    """暴露给 `ToolRepo` 的 seed fixture 查询接口。"""

    @classmethod
    def ensure_discovered(cls) -> None:
        """扫描 `builtin/` 下所有模块并 import,触发 @builtin_tool 副作用。

        幂等：已发现过则直接返回。
        """
        global _DISCOVERED
        if _DISCOVERED:
            return
        pkg = __import__("chariot.tools.builtin", fromlist=["builtin"])
        for _, name, _ in pkgutil.iter_modules(pkg.__path__):
            if name.startswith("_"):
                continue
            importlib.import_module(f"chariot.tools.builtin.{name}")
        _DISCOVERED = True

    @classmethod
    def seed_entries(cls) -> list[tuple[str, str, dict[str, Any]]]:
        """返回按注册顺序的 builtin tool seed fixture 列表。

        调用前会自动 `ensure_discovered()`。
        格式与旧 `_SEED_FIXTURES` 一致: list[(name, type, options)]
        """
        cls.ensure_discovered()
        return list(_BUILTIN_SEEDS)

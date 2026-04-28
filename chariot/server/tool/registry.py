"""`ToolRegistry` —— 按 type 名字派发 `Tool` 构造(类比 ModelRegistry)。

把"type 字符串(`tools` 表 type 列)→ 对应 Tool 类的 from_config"关系收在
一个类里。Agent 在 lifespan 通过 `ToolRegistry.build(entry)` 构造实例,
完全不感知"有哪些工具"。

使用
----
1. 在 tool 类上实现 `from_config(entry) -> Self` classmethod
2. 在 `chariot/server/tool/__init__.py` 显式注册:

   ```python
   ToolRegistry.register("read_file", ReadFileTool)
   ToolRegistry.register("list_dir", ListDirTool)
   ToolRegistry.register("shell_exec", ShellExecTool)
   ToolRegistry.register("http_get", HttpGetTool)
   ```

3. 调用方:`tool = ToolRegistry.build(entry)`(`entry.type` 派发到 builder)

加新工具 = 实现一个 tool 类 + 在 `__init__.py` 加一行 `register(...)`,不动
Agent / Controller / lifespan。

模块级零自由函数 / 零可变变量,所有状态挂在 `ToolRegistry` ClassVar 上。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from chariot.server.config import ConfigError, ToolEntry
from chariot.server.tool.base import Tool

# 每个 builder 是一个 callable:ToolEntry → Tool 实例
ToolBuilder = Callable[[ToolEntry], Tool]


class ToolRegistry:
    """type_name → `Tool.from_config` 的注册中心(类级单例)。

    类承担三件事(完全镜像 ModelRegistry):
    1. `register(type_name, tool_cls)`:把 `tool_cls.from_config` 收进 `_builders`
    2. `build(entry)`:按 `entry.type` 派发到 builder,调用它构造 Tool
    3. `known_types()`:枚举所有已注册 type 名

    模块级不暴露任何状态;`_builders` ClassVar 是唯一注册表。
    """

    _builders: ClassVar[dict[str, ToolBuilder]] = {}

    # ---- 注册 ----

    @classmethod
    def register(cls, type_name: str, tool_cls: type[Tool]) -> None:
        """显式注册一个 tool 类。

        - type 重复 → `ValueError`
        - `tool_cls` 静态保证是 `type[Tool]`,`from_config` 必有
        - 调用时机:`chariot/server/tool/__init__.py` 模块加载阶段集中调用
        """
        if type_name in cls._builders:
            raise ValueError(f"重复注册 tool type: {type_name!r}")
        cls._builders[type_name] = tool_cls.from_config

    # ---- 构造 ----

    @classmethod
    def build(cls, entry: ToolEntry) -> Tool:
        """按 `entry.type` 派发到对应 `from_config(entry)`。

        type 未注册 → `ConfigError`(startup 期 raise)。
        """
        try:
            builder = cls._builders[entry.type]
        except KeyError as e:
            known = ", ".join(cls.known_types()) or "(空)"
            raise ConfigError(
                f"未知 tool type: {entry.type!r} (tool name={entry.name!r});已注册:{known}"
            ) from e
        return builder(entry)

    @classmethod
    def known_types(cls) -> list[str]:
        return sorted(cls._builders)

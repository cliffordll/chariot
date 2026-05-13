"""chariot 工具实现层。

`BaseTool` 是协议接口(`base.py`),任何具体工具都要实现 `create(entry)` /
`schema()` / `execute(input)`。

Agent 在 lifespan 通过 `ToolRegistry.build(entry)` 构造实例,放进
`Agent._tools: dict[str, BaseTool]`;调 Model 前把所有 enabled tools 的 schema
塞进 `body.tools`(若 client 已传则不覆盖,见 docs/DESIGN.md §M.3)。

注册方式:
- 每个 builtin 工具类用 `@builtin_tool(defaults={...})` 装饰器(在
  `chariot/tools/builtin/_meta.py` 中定义)
- `chariot/tools/__init__.py` 在模块加载时调用 `BuiltinToolMeta.ensure_discovered()`
  扫描 `builtin/` 下所有模块,触发装饰器副作用 → 自动注册到 `ToolRegistry` +
  收集 seed fixture

加新工具:
1. 写一个新文件 `chariot/tools/builtin/<name>.py`
2. 类上加 `@builtin_tool(defaults={...})`
3. 什么都不用改,启动时自动出现在 DB 和 Registry 中
"""

from __future__ import annotations

from chariot.tools.builtin._meta import BuiltinToolMeta, builtin_tool  # noqa: F401
from chariot.tools.registry import ToolRegistry  # noqa: F401

# 模块加载时触发一次扫描(幂等)
BuiltinToolMeta.ensure_discovered()

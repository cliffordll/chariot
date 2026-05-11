"""chariot 工具实现层(0.4.0)。

`BaseTool` 是协议接口(`base.py`),任何具体工具都要实现 `create(entry)` /
`schema()` / `execute(input)`。

Agent 在 lifespan 通过 `ToolRegistry.build(entry)` 构造实例,放进
`Agent._tools: dict[str, BaseTool]`;调 Model 前把所有 enabled tools 的 schema
塞进 `body.tools`(若 client 已传则不覆盖,见 docs/DESIGN.md §M.3)。

注册集中在本文件(显式调用,非 import 副作用):

    ToolRegistry.register("read_file", ReadFileTool)
    ToolRegistry.register("list_dir", ListDirTool)
    ToolRegistry.register("shell_exec", ShellExecTool)
    ToolRegistry.register("http_get", HttpGetTool)
    ToolRegistry.register("propose_skill", ProposeSkillTool)  # B6 wave 3

加新工具:写一个新文件 `chariot/tools/builtin/<name>.py` + 在本文件加一行
`ToolRegistry.register("type_name", NewTool)`。
"""

from __future__ import annotations

from chariot.tools.builtin.http_get import HttpGetTool
from chariot.tools.builtin.list_dir import ListDirTool
from chariot.tools.builtin.propose_skill import ProposeSkillTool
from chariot.tools.builtin.read_file import ReadFileTool
from chariot.tools.builtin.shell_exec import ShellExecTool
from chariot.tools.registry import ToolRegistry

ToolRegistry.register("read_file", ReadFileTool)
ToolRegistry.register("list_dir", ListDirTool)
ToolRegistry.register("shell_exec", ShellExecTool)
ToolRegistry.register("http_get", HttpGetTool)
ToolRegistry.register("propose_skill", ProposeSkillTool)

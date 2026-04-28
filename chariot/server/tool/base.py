"""Tool 接口定义 —— Agent 工具调用的抽象基类(0.4.0)。

任何具体工具(read_file / list_dir / shell_exec / http_get / 未来扩展)继承
`Tool` 并实现:

- `from_config(entry)` classmethod —— `ToolRegistry` 用它构造实例
- `schema()` —— 返回 Anthropic tool definition JSON
- `execute(input)` async —— 跑工具,返 tool_result content block

ABC + abstractmethod 强制子类实现这些(缺则实例化即抛 TypeError);
`ToolRegistry.register` 因此不必在运行期再做 callable 兜底校验。

职责边界(严格,类比 Model 接口)
-------------------------------
- **无状态**:每次 execute 独立,实例之间不共享内存,失败不影响下一次
- **不碰 DB**:Tool 只做"输入 → 输出";`messages` 表持久化由 Agent 负责
- **不感知 conversation_id**:Tool 不知道"这是哪个会话的第几轮工具调用"

返回形态:`execute` 返 anthropic tool_result content block 形态,
`tool_use_id` 字段由 Agent 在工具循环里填(Tool 不管对应哪个 tool_use)。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Self

from chariot.server.config import ToolEntry


class Tool(ABC):
    """Agent 工具调用抽象基类。

    实现 checklist:
    1. `name: str` 实例属性 —— 跟 ToolEntry.name 一致(用户面 ID)
    2. `from_config(entry) -> Self` classmethod —— `ToolRegistry` 构造契约;
       options 不合法 raise `ConfigError`
    3. `schema() -> dict` —— Anthropic tool definition JSON
       (`{"name", "description", "input_schema"}`),Agent 调 Model 前注入
       到 `body.tools`
    4. `execute(input) -> dict` async —— 返 anthropic tool_result block
       (`{"type": "tool_result", "content": [...], "is_error"?: bool}`);
       `tool_use_id` 由 Agent 填,**这里不要写**
    """

    name: str

    @classmethod
    @abstractmethod
    def from_config(cls, entry: ToolEntry) -> Self:
        """从 ToolEntry 构造实例(读 entry.name + entry.options)。"""

    @abstractmethod
    def schema(self) -> dict[str, Any]:
        """返回 Anthropic tool definition JSON。"""

    @abstractmethod
    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        """跑工具,返 tool_result content block(不含 tool_use_id)。"""

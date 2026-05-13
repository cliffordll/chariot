"""TodoTool — Session 内任务列表管理工具(0.8.7)。

设计:状态挂在 AIAgent 实例上(非持久化),session 结束即清空。
每次调用返完整列表,agent 通过上下文看到当前任务进度。

input
-----
- `action` (str):"add" | "update" | "remove" | "list" | "clear"
- `id` (str):add/update/remove 用,agent 自分配标识
- `text` (str):add/update 用
- `status` (str):"pending" | "in_progress" | "completed" | "cancelled"(update 用)

options: 无(TodoTool 无持久化配置)
"""

from __future__ import annotations

from typing import Any, ClassVar, Self

from chariot.models.tool import ToolEntry
from chariot.tools.base import BaseTool
from chariot.tools.builtin._meta import builtin_tool

_VALID_STATUSES = {"pending", "in_progress", "completed", "cancelled"}


@builtin_tool(defaults={})
class TodoTool(BaseTool):
    """Session 内任务列表管理。"""

    _DESCRIPTION: ClassVar[str] = (
        "Manage a todo list for the current session. "
        "Use this to decompose complex tasks, track progress, and stay focused. "
        "The list lives only for this conversation and is cleared when the session ends."
    )

    def __init__(self, name: str) -> None:
        self.name = name

    @classmethod
    def create(cls, entry: ToolEntry) -> Self:
        return cls(name=entry.name)

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self._DESCRIPTION,
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add", "update", "remove", "list", "clear"],
                        "description": "Action to perform on the todo list.",
                    },
                    "id": {
                        "type": "string",
                        "description": "Unique identifier for the todo item "
                        "(agent-assigned). Required for add/update/remove.",
                    },
                    "text": {
                        "type": "string",
                        "description": "Todo item text. Required for add/update.",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "completed", "cancelled"],
                        "description": "Status for update action.",
                    },
                },
                "required": ["action"],
            },
        }

    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        # TodoStore 从 context 中取(由 AIAgent 在 session 初始化时注入)
        # 这里用特殊约定:通过 input 中的 _todo_store 字段传递
        # 实际运行时由 AgentLoop 注入
        store = input.get("_todo_store")
        if store is None:
            store = _TodoStore()
            input["_todo_store"] = store

        action = input.get("action")
        if action not in ("add", "update", "remove", "list", "clear"):
            return self._error(f"未知 action: {action!r}")

        item_id = input.get("id")
        text = input.get("text")
        status = input.get("status")

        if action == "add":
            if not isinstance(item_id, str) or not item_id:
                return self._error("add 需要 id(非空字符串)")
            if not isinstance(text, str) or not text:
                return self._error("add 需要 text(非空字符串)")
            store.add(item_id, text)
        elif action == "update":
            if not isinstance(item_id, str) or not item_id:
                return self._error("update 需要 id")
            if text is None and status is None:
                return self._error("update 至少需要 text 或 status 之一")
            if status is not None and status not in _VALID_STATUSES:
                return self._error(f"无效 status: {status!r}")
            store.update(item_id, text=text, status=status)
        elif action == "remove":
            if not isinstance(item_id, str) or not item_id:
                return self._error("remove 需要 id")
            store.remove(item_id)
        elif action == "clear":
            store.clear()
        # list: 什么也不做,直接返当前列表

        return {
            "type": "tool_result",
            "content": [{"type": "text", "text": store.format()}],
        }

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {
            "type": "tool_result",
            "content": [{"type": "text", "text": message}],
            "is_error": True,
        }


class _TodoStore:
    """内存级 todo 列表。一个实例对应一个 session。"""

    def __init__(self) -> None:
        self._items: dict[str, dict[str, str]] = {}
        self._order: list[str] = []

    def add(self, item_id: str, text: str) -> None:
        self._items[item_id] = {"text": text, "status": "pending"}
        if item_id not in self._order:
            self._order.append(item_id)

    def update(self, item_id: str, text: str | None = None, status: str | None = None) -> None:
        if item_id not in self._items:
            return
        if text is not None:
            self._items[item_id]["text"] = text
        if status is not None:
            self._items[item_id]["status"] = status

    def remove(self, item_id: str) -> None:
        self._items.pop(item_id, None)
        if item_id in self._order:
            self._order.remove(item_id)

    def clear(self) -> None:
        self._items.clear()
        self._order.clear()

    def format(self) -> str:
        if not self._order:
            return "📝 当前无任务"
        lines = ["📝 任务列表", ""]
        status_emoji = {
            "pending": "⬜",
            "in_progress": "🔄",
            "completed": "✅",
            "cancelled": "❌",
        }
        for item_id in self._order:
            item = self._items.get(item_id)
            if item is None:
                continue
            emoji = status_emoji.get(item["status"], "⬜")
            lines.append(f"{emoji} [{item_id}] {item['text']}")
        lines.append("")
        lines.append(f"总计: {len(self._order)} 项")
        return "\n".join(lines)

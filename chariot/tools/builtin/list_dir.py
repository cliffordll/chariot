"""ListDirTool — 列目录条目(0.4.0 内置工具)。

options
-------
(无)

输入 schema
-----------
- `path` (str, required):目录路径
- `recursive` (bool, optional):递归遍历,默认 False(只列直接子项)

返回
----
anthropic tool_result content block:
- 成功:`{"type": "tool_result", "content": [{"type": "text", "text": "<json>"}]}`
  text 是 `[{name, type: 'file'|'dir'|'other', size}]` 的 JSON(file size = 文件字节数,
  dir size = 0)
- 失败:`is_error=True`(路径不存在 / 不是目录 / 无权限)

模块级零自由函数,所有逻辑收在 `ListDirTool` 类里。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar, Self

from chariot.models.tool import ToolEntry
from chariot.tools.base import BaseTool
from chariot.tools.builtin._meta import builtin_tool


@builtin_tool(defaults={})
class ListDirTool(BaseTool):
    """列目录工具。返回 JSON 数组,可选递归。"""

    _DESCRIPTION: ClassVar[str] = (
        "List entries (files / directories) in a directory. "
        "Returns a JSON array of {name, type, size} objects. "
        "Set recursive=true to walk the tree."
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
                    "path": {"type": "string", "description": "Directory path."},
                    "recursive": {
                        "type": "boolean",
                        "description": "Walk recursively (default false).",
                    },
                },
                "required": ["path"],
            },
        }

    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        path_raw = input.get("path")
        if not isinstance(path_raw, str) or not path_raw:
            return self._error("input.path 必须是非空字符串")
        recursive = bool(input.get("recursive", False))

        path = self.normalize_path(path_raw)
        try:
            if not path.exists():
                return self._error(f"路径不存在: {path}")
            if not path.is_dir():
                return self._error(f"不是目录: {path}")
            entries = self._walk(path, recursive=recursive)
        except OSError as e:
            return self._error(f"列目录失败: {e}")

        return {
            "type": "tool_result",
            "content": [
                {"type": "text", "text": json.dumps(entries, ensure_ascii=False)},
            ],
        }

    @staticmethod
    def _walk(root: Path, *, recursive: bool) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        iterator = root.rglob("*") if recursive else root.iterdir()
        for p in iterator:
            try:
                if p.is_file():
                    items.append(
                        {
                            "name": str(p.relative_to(root)) if recursive else p.name,
                            "type": "file",
                            "size": p.stat().st_size,
                        }
                    )
                elif p.is_dir():
                    items.append(
                        {
                            "name": str(p.relative_to(root)) if recursive else p.name,
                            "type": "dir",
                            "size": 0,
                        }
                    )
                else:
                    items.append(
                        {
                            "name": str(p.relative_to(root)) if recursive else p.name,
                            "type": "other",
                            "size": 0,
                        }
                    )
            except OSError:
                # 单条 stat 失败不影响其它条目;跳过
                continue
        # 排序:dir 优先,其次按 name
        items.sort(key=lambda it: (it["type"] != "dir", it["name"]))
        return items

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {
            "type": "tool_result",
            "content": [{"type": "text", "text": message}],
            "is_error": True,
        }

"""EditFileTool — 编辑文件工具(0.8.7)。

在文件中查找 old_string 替换为 new_string。支持多行文本。
这是 LLM 最自然的编辑方式,比 diff/patch 更直观。

options
-------
- `max_occurrences` (int):最大替换次数,默认 1;0 表示替换全部
"""

from __future__ import annotations

from typing import Any, ClassVar, Self

from chariot.agent.exceptions import ConfigError
from chariot.models.tool import ToolEntry
from chariot.tools.base import BaseTool


class EditFileTool(BaseTool):
    """编辑文件工具。old_string → new_string 文本替换。"""

    _DESCRIPTION: ClassVar[str] = (
        "Edit a file by replacing old_string with new_string. "
        "If old_string occurs multiple times, only the first occurrence is replaced "
        "unless max_occurrences is configured or specified."
    )

    def __init__(
        self,
        name: str,
        max_occurrences: int,
    ) -> None:
        self.name = name
        self.max_occurrences = max_occurrences

    @classmethod
    def create(cls, entry: ToolEntry) -> Self:
        max_occurrences = entry.options.get("max_occurrences", 1)
        if not isinstance(max_occurrences, int) or max_occurrences < 0:
            raise ConfigError(f"edit_file.options.max_occurrences 必须是非负整数,得到 {max_occurrences!r}")
        return cls(name=entry.name, max_occurrences=max_occurrences)

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self._DESCRIPTION,
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or relative path to the file.",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "The text to find and replace.",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "The replacement text.",
                    },
                    "occurrence": {
                        "type": "integer",
                        "description": "Which occurrence to replace (1-based). 0 means replace all.",
                        "default": 1,
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
        }

    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        path_raw = input.get("path")
        old_string = input.get("old_string")
        new_string = input.get("new_string")
        occurrence = input.get("occurrence", 1)

        if not isinstance(path_raw, str) or not path_raw:
            return self._error("input.path 必须是非空字符串")
        if not isinstance(old_string, str):
            return self._error("input.old_string 必须是字符串")
        if not isinstance(new_string, str):
            return self._error("input.new_string 必须是字符串")
        if not isinstance(occurrence, int) or occurrence < 0:
            return self._error("input.occurrence 必须是非负整数")

        path = self.normalize_path(path_raw)

        try:
            if not path.exists():
                return self._error(f"文件不存在: {path}")
            if not path.is_file():
                return self._error(f"不是文件: {path}")
            content = path.read_text(encoding="utf-8")
        except OSError as e:
            return self._error(f"读文件失败: {e}")

        # 查找
        count = content.count(old_string)
        if count == 0:
            return self._error(
                f"未找到 old_string (文件共 {len(content)} 字符)。提示:old_string 必须完全匹配(含空白)。"
            )

        # 决定替换策略
        if occurrence == 0:
            # 替换全部
            new_content = content.replace(old_string, new_string)
            replaced_count = count
        else:
            if occurrence > count:
                return self._error(f"old_string 共出现 {count} 次,指定 occurrence={occurrence} 超出范围")
            # 只替换第 occurrence 次
            parts = content.split(old_string)
            # parts 长度为 count+1
            # 替换第 occurrence 次:把 parts[occurrence-1] 和 parts[occurrence] 之间的 old_string 换成 new_string
            new_content = old_string.join(parts[:occurrence]) + new_string + old_string.join(parts[occurrence:])
            replaced_count = 1

        # 检查 max_occurrences 限制
        if occurrence == 0 and self.max_occurrences > 0 and replaced_count > self.max_occurrences:
            return self._error(
                f"old_string 出现 {count} 次,超过 max_occurrences={self.max_occurrences};"
                f"请缩小 old_string 范围或增大 max_occurrences"
            )

        # 写入
        try:
            path.write_text(new_content, encoding="utf-8")
        except OSError as e:
            return self._error(f"写文件失败: {e}")

        return {
            "type": "tool_result",
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"替换成功: {path}\n"
                        f"- old_string 出现 {count} 次\n"
                        f"- 替换了 {replaced_count} 处\n"
                        f"- 文件从 {len(content)} 字符变为 {len(new_content)} 字符"
                    ),
                }
            ],
        }

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {
            "type": "tool_result",
            "content": [{"type": "text", "text": message}],
            "is_error": True,
        }

"""WriteFileTool — 写文件工具(0.8.7)。

支持覆盖/追加,目录不存在自动创建。配合 guardrails 限制写范围。

options
-------
- `default_mode` (str):"overwrite" | "append",默认 "overwrite"
- `allow_outside_cwd` (bool):默认 False;True 时绕过 cwd 外写入限制
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Self

from chariot.agent.exceptions import ConfigError
from chariot.models.tool import ToolEntry
from chariot.tools.base import BaseTool
from chariot.tools.builtin._meta import builtin_tool


@builtin_tool(defaults={"default_mode": "overwrite", "allow_outside_cwd": False})
class WriteFileTool(BaseTool):
    """写文件工具。支持覆盖/追加,目录不存在自动创建。"""

    _DESCRIPTION: ClassVar[str] = (
        "Write text content to a file. "
        "Directories are created automatically if they do not exist. "
        "Use mode='append' to append instead of overwrite."
    )

    def __init__(
        self,
        name: str,
        default_mode: str,
        allow_outside_cwd: bool,
    ) -> None:
        self.name = name
        self.default_mode = default_mode
        self.allow_outside_cwd = allow_outside_cwd

    @classmethod
    def create(cls, entry: ToolEntry) -> Self:
        default_mode = entry.options.get("default_mode", "overwrite")
        allow_outside_cwd = entry.options.get("allow_outside_cwd", False)
        if default_mode not in ("overwrite", "append"):
            raise ConfigError(f"write_file.options.default_mode 必须是 'overwrite' 或 'append',得到 {default_mode!r}")
        if not isinstance(allow_outside_cwd, bool):
            raise ConfigError(f"write_file.options.allow_outside_cwd 必须是 bool,得到 {allow_outside_cwd!r}")
        return cls(
            name=entry.name,
            default_mode=default_mode,
            allow_outside_cwd=allow_outside_cwd,
        )

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
                    "content": {
                        "type": "string",
                        "description": "Text content to write.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["overwrite", "append"],
                        "description": "Write mode. Defaults to tool config default_mode.",
                    },
                },
                "required": ["path", "content"],
            },
        }

    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        path_raw = input.get("path")
        content_raw = input.get("content")
        if not isinstance(path_raw, str) or not path_raw:
            return self._error("input.path 必须是非空字符串")
        if not isinstance(content_raw, str):
            return self._error("input.content 必须是字符串")

        path = self.normalize_path(path_raw)
        mode = input.get("mode", self.default_mode)
        if mode not in ("overwrite", "append"):
            return self._error(f"mode 必须是 'overwrite' 或 'append',得到 {mode!r}")

        # guardrail: 默认禁止写 cwd 外
        if not self.allow_outside_cwd:
            try:
                cwd = Path.cwd().resolve()
                target = path.resolve()
                if cwd not in target.parents and target != cwd:
                    return self._error(
                        f"禁止写入工作目录外: {path} (cwd={cwd});如需放开,设 write_file.options.allow_outside_cwd=true"
                    )
            except OSError as e:
                return self._error(f"路径解析失败: {e}")

        # 创建父目录
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return self._error(f"创建目录失败: {e}")

        # 写入
        try:
            if mode == "append":
                with path.open("a", encoding="utf-8") as f:
                    f.write(content_raw)
            else:
                path.write_text(content_raw, encoding="utf-8")
        except OSError as e:
            return self._error(f"写文件失败: {e}")

        action = "覆盖" if mode == "overwrite" else "追加"
        return {
            "type": "tool_result",
            "content": [{"type": "text", "text": f"{action}成功: {path} ({len(content_raw)} 字符)"}],
        }

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {
            "type": "tool_result",
            "content": [{"type": "text", "text": message}],
            "is_error": True,
        }

"""ReadFileTool — 读单个文件内容(0.4.0 内置工具)。

options
-------
- `max_bytes` (int):截断阈值,默认 1 MiB(1048576);超过则只返前 max_bytes 字节
  并标记 `truncated=True`

输入 schema
-----------
- `path` (str, required):文件路径(绝对或相对当前工作目录)

返回
----
anthropic tool_result content block:
- 成功:`{"type": "tool_result", "content": [{"type": "text", "text": "..."}]}`
  - 内容 UTF-8 解码失败 → fallback base64,文本前加 `[base64]` 标记
  - 文件超过 max_bytes → 只返前 max_bytes,文本末加 `... [truncated, total=N bytes]`
- 失败:`{"type": "tool_result", "content": [{"type": "text", "text": "<error>"}], "is_error": True}`
  - 路径不存在 / 不是文件 / 无权限 → is_error=True

安全注意
--------
**0.4.0 不限路径**:LLM 拿到 read_file 后理论上可以读用户机器上任何文件
(只要进程有权限)。用户在 admin/tools 启用此工具时需自行评估风险,
chariot 默认 disabled 即此考虑。0.4.x 可能加 path_whitelist 选项。

模块级零自由函数,所有逻辑收在 `ReadFileTool` 类里。
"""

from __future__ import annotations

import base64
from typing import Any, ClassVar, Self

from chariot.models.tool import ToolEntry
from chariot.tools.base import BaseTool
from chariot.tools.builtin._meta import builtin_tool

_DEFAULT_MAX_BYTES = 1048576  # 1 MiB


@builtin_tool(defaults={"max_bytes": _DEFAULT_MAX_BYTES})
class ReadFileTool(BaseTool):
    """读文件工具,内容超大截断 + UTF-8 fallback base64。"""

    _DESCRIPTION: ClassVar[str] = (
        "Read the contents of a file from the local filesystem. "
        "Returns up to max_bytes bytes (configured server-side); "
        "binary files are base64-encoded as a fallback."
    )

    def __init__(self, name: str, max_bytes: int) -> None:
        self.name = name
        self.max_bytes = max_bytes

    @classmethod
    def create(cls, entry: ToolEntry) -> Self:
        max_bytes = entry.options.get("max_bytes", _DEFAULT_MAX_BYTES)
        if not isinstance(max_bytes, int) or max_bytes <= 0:
            from chariot.agent.config import ConfigError

            raise ConfigError(f"read_file.options.max_bytes 必须是正整数,得到 {max_bytes!r}")
        return cls(name=entry.name, max_bytes=max_bytes)

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
                },
                "required": ["path"],
            },
        }

    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        path_raw = input.get("path")
        if not isinstance(path_raw, str) or not path_raw:
            return self._error("input.path 必须是非空字符串")
        path = self.normalize_path(path_raw)
        try:
            if not path.exists():
                return self._error(f"路径不存在: {path}")
            if not path.is_file():
                return self._error(f"不是文件: {path}")
            raw = path.read_bytes()
        except OSError as e:
            return self._error(f"读文件失败: {e}")

        truncated = len(raw) > self.max_bytes
        clipped = raw[: self.max_bytes]
        try:
            text = clipped.decode("utf-8")
        except UnicodeDecodeError:
            text = f"[base64] {base64.b64encode(clipped).decode('ascii')}"
        if truncated:
            text += f"\n... [truncated, total={len(raw)} bytes]"

        return {
            "type": "tool_result",
            "content": [{"type": "text", "text": text}],
        }

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {
            "type": "tool_result",
            "content": [{"type": "text", "text": message}],
            "is_error": True,
        }

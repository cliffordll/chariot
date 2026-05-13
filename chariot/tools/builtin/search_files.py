"""SearchFilesTool — 文件内容搜索工具(0.8.7)。

递归搜索目录下文件内容,支持文本匹配或正则。返回匹配行及上下文。

options
-------
- `max_results` (int):最大返回结果数,默认 50
- `max_file_size_mb` (float):跳过超过此大小的文件(MB),默认 10
- `context_lines` (int):匹配行上下文的行数,默认 2
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, ClassVar, Self

from chariot.agent.exceptions import ConfigError
from chariot.models.tool import ToolEntry
from chariot.tools.base import BaseTool
from chariot.tools.builtin._meta import builtin_tool


@builtin_tool(defaults={"max_results": 50, "max_file_size_mb": 10.0, "context_lines": 2})
class SearchFilesTool(BaseTool):
    """文件内容搜索工具。grep + find 的合体。"""

    _DESCRIPTION: ClassVar[str] = (
        "Search for text or regex patterns inside files recursively. Returns matching lines with surrounding context."
    )

    def __init__(
        self,
        name: str,
        max_results: int,
        max_file_size_mb: float,
        context_lines: int,
    ) -> None:
        self.name = name
        self.max_results = max_results
        self.max_file_size_mb = max_file_size_mb
        self.context_lines = context_lines

    @classmethod
    def create(cls, entry: ToolEntry) -> Self:
        max_results = entry.options.get("max_results", 50)
        max_file_size_mb = entry.options.get("max_file_size_mb", 10.0)
        context_lines = entry.options.get("context_lines", 2)
        if not isinstance(max_results, int) or max_results <= 0:
            raise ConfigError(f"search_files.options.max_results 必须是正整数,得到 {max_results!r}")
        if not isinstance(max_file_size_mb, (int, float)) or max_file_size_mb <= 0:
            raise ConfigError(f"search_files.options.max_file_size_mb 必须是正数,得到 {max_file_size_mb!r}")
        if not isinstance(context_lines, int) or context_lines < 0:
            raise ConfigError(f"search_files.options.context_lines 必须是非负整数,得到 {context_lines!r}")
        return cls(
            name=entry.name,
            max_results=max_results,
            max_file_size_mb=float(max_file_size_mb),
            context_lines=context_lines,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self._DESCRIPTION,
            "input_schema": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Text or regex pattern to search for.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Root directory to search in. Default is current directory.",
                        "default": ".",
                    },
                    "file_extension": {
                        "type": "string",
                        "description": "Filter by file extension, e.g. '.py'. Null means all files.",
                    },
                    "use_regex": {
                        "type": "boolean",
                        "description": "Whether pattern is a regex. Default false.",
                        "default": False,
                    },
                },
                "required": ["pattern"],
            },
        }

    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        pattern = input.get("pattern")
        path_raw = input.get("path", ".")
        file_extension = input.get("file_extension")
        use_regex = input.get("use_regex", False)

        if not isinstance(pattern, str) or not pattern:
            return self._error("input.pattern 必须是非空字符串")
        if not isinstance(path_raw, str):
            return self._error("input.path 必须是字符串")
        if file_extension is not None and not isinstance(file_extension, str):
            return self._error("input.file_extension 必须是字符串或 null")
        if not isinstance(use_regex, bool):
            return self._error("input.use_regex 必须是布尔值")

        root = self.normalize_path(path_raw)
        if not root.exists():
            return self._error(f"目录不存在: {root}")
        if not root.is_dir():
            return self._error(f"不是目录: {root}")

        # 编译正则(如果需要)
        if use_regex:
            try:
                regex = re.compile(pattern)
            except re.error as e:
                return self._error(f"正则表达式无效: {e}")
        else:
            regex = None

        max_bytes = int(self.max_file_size_mb * 1024 * 1024)
        results: list[dict[str, Any]] = []
        total_files_searched = 0

        try:
            for dirpath, _dirnames, filenames in os.walk(root):
                for filename in filenames:
                    if file_extension and not filename.endswith(file_extension):
                        continue
                    filepath = Path(dirpath) / filename
                    total_files_searched += 1

                    # 跳过超大文件
                    try:
                        size = filepath.stat().st_size
                        if size > max_bytes:
                            continue
                    except OSError:
                        continue

                    # 读取并搜索
                    try:
                        text = filepath.read_text(encoding="utf-8", errors="replace")
                    except (OSError, UnicodeDecodeError):
                        continue

                    lines = text.splitlines()
                    for i, line in enumerate(lines):
                        matched = regex.search(line) is not None if regex else pattern in line
                        if matched:
                            # 上下文
                            start = max(0, i - self.context_lines)
                            end = min(len(lines), i + self.context_lines + 1)
                            context = "\n".join(f"{idx + 1:4d}: {lines[idx]}" for idx in range(start, end))
                            results.append(
                                {
                                    "file": str(filepath),
                                    "line": i + 1,
                                    "match": line.strip(),
                                    "context": context,
                                }
                            )
                            if len(results) >= self.max_results:
                                break
                    if len(results) >= self.max_results:
                        break
                if len(results) >= self.max_results:
                    break
        except OSError as e:
            return self._error(f"搜索过程中出错: {e}")

        if not results:
            return {
                "type": "tool_result",
                "content": [
                    {
                        "type": "text",
                        "text": (f"未找到匹配: pattern={pattern!r}, path={root}, 搜索了 {total_files_searched} 个文件"),
                    }
                ],
            }

        # 格式化输出
        lines_text = []
        for r in results:
            lines_text.append(f"📄 {r['file']}:{r['line']}")
            lines_text.append(r["context"])
            lines_text.append("")

        summary = f"共 {len(results)} 处匹配(搜索了 {total_files_searched} 个文件)"
        return {
            "type": "tool_result",
            "content": [{"type": "text", "text": summary + "\n\n" + "\n".join(lines_text)}],
        }

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {
            "type": "tool_result",
            "content": [{"type": "text", "text": message}],
            "is_error": True,
        }

"""GitStatusTool — Git 仓库状态工具(0.8.7)。

只读工具。返回当前 git 仓库的分支、改动文件、未跟踪文件等信息。
比 shell_exec `git status` 更安全且输出结构化。

options: 无
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar, Self

from chariot.models.tool import ToolEntry
from chariot.tools.base import BaseTool


class GitStatusTool(BaseTool):
    """Git 仓库状态(只读)。"""

    _DESCRIPTION: ClassVar[str] = (
        "Show the current git repository status: branch name, "
        "ahead/behind remote, modified files, untracked files. "
        "Read-only; does not modify the repository."
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
                    "path": {
                        "type": "string",
                        "description": "Path to the git repository. Default is current directory.",
                        "default": ".",
                    },
                    "include_diff": {
                        "type": "boolean",
                        "description": "Include diff summary for modified files. Default false.",
                        "default": False,
                    },
                },
            },
        }

    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        path_raw = input.get("path", ".")
        include_diff = input.get("include_diff", False)

        if not isinstance(path_raw, str):
            return self._error("input.path 必须是字符串")
        if not isinstance(include_diff, bool):
            return self._error("input.include_diff 必须是布尔值")

        repo_path = self.normalize_path(path_raw)
        if not repo_path.exists():
            return self._error(f"路径不存在: {repo_path}")

        # 检查是否是 git 仓库
        git_dir = repo_path / ".git"
        if not git_dir.exists():
            # 向上查找
            found = False
            for parent in repo_path.parents:
                if (parent / ".git").exists():
                    repo_path = parent
                    found = True
                    break
            if not found:
                return self._error(f"未找到 git 仓库: {path_raw} 及其父目录均无 .git")

        # 获取状态
        try:
            branch = await self._git_command(repo_path, ["git", "rev-parse", "--abbrev-ref", "HEAD"])
            status_short = await self._git_command(repo_path, ["git", "status", "--short"])
            import contextlib

            ahead_behind = ""
            with contextlib.suppress(Exception):
                ahead_behind = await self._git_command(
                    repo_path, ["git", "rev-list", "--left-right", "--count", "HEAD...@{upstream}"]
                )

            diff_stat = ""
            if include_diff:
                with contextlib.suppress(Exception):
                    diff_stat = await self._git_command(repo_path, ["git", "diff", "--stat"])
        except Exception as e:
            return self._error(f"git 命令失败: {e}")

        # 格式化输出
        lines = [f"🌿 分支: {branch.strip()}", ""]

        if ahead_behind.strip():
            parts = ahead_behind.strip().split("\t")
            if len(parts) == 2:
                ahead, behind = parts
                lines.append(f"📤 领先 remote: {ahead} commit(s)")
                lines.append(f"📥 落后 remote: {behind} commit(s)")
                lines.append("")

        if status_short.strip():
            lines.append("📂 改动文件:")
            for line in status_short.strip().splitlines():
                lines.append(f"  {line}")
        else:
            lines.append("✨ 工作区干净,无改动")

        if include_diff and diff_stat.strip():
            lines.append("")
            lines.append("📊 Diff 统计:")
            lines.append(diff_stat.strip())

        return {
            "type": "tool_result",
            "content": [{"type": "text", "text": "\n".join(lines)}],
        }

    async def _git_command(self, cwd: Path, cmd: list[str]) -> str:
        """在指定目录执行 git 命令,返 stdout。"""
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"git 命令失败: {' '.join(cmd)}\n{stderr.decode('utf-8', errors='replace')}")
        return stdout.decode("utf-8", errors="replace")

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {
            "type": "tool_result",
            "content": [{"type": "text", "text": message}],
            "is_error": True,
        }

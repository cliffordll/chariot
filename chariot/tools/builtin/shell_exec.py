"""ShellExecTool — 在沙盒目录跑可执行命令(0.4.0 内置工具)。

options
-------
- `workdir` (str):沙盒根,`~` 自动展开;不存在则首次执行时 mkdir -p
- `timeout_s` (int):每次命令超时秒数,默认 30

输入 schema
-----------
- `cmd` (str, required):**可执行文件路径或 PATH 上的命令名**(`python` / `git` /
  绝对路径),不走 shell —— shell 内置(`echo` / `cd` / pipe / glob)不可用
- `args` (list[str], optional):参数列表(原样传给子进程,无 shell quoting 介入)

跑法
----
直接 `asyncio.create_subprocess_exec(cmd, *args)`,跨平台一致,无 PowerShell /
bash 分支(避免 quoting 噩梦)。要用管道 / glob,LLM 在工具循环里多步拆开。

返回
----
- 成功:`tool_result` 含 `{stdout, stderr, exit_code, timed_out}` 的 JSON
- 失败:is_error=True(workdir 准备失败 / 子进程启动失败 / 命令找不到)

安全注意
--------
- **workdir 只是 cwd**,Python `cwd=` 仅影响相对路径解析。命令本身可以读写
  workdir 外的任何路径(进程权限范围内),0.4.0 接受这点
- **不接受 shell metachar**(管道 / 重定向 / 通配符);LLM 无法用 `; rm -rf /`
  之类一次性拼起做坏事 —— 这是 create_subprocess_exec 不走 shell 的免费收益
- 0.4.x 可能加 cmd 白名单或 namespaced 沙盒(Docker / firejail)

模块级零自由函数,所有逻辑收在 `ShellExecTool` 类里。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, ClassVar, Self, cast

from chariot.agent.config import ConfigError, ToolEntry
from chariot.tools.base import BaseTool

_DEFAULT_TIMEOUT_S = 30
_DEFAULT_WORKDIR = "~/.chariot/sandbox"


class ShellExecTool(BaseTool):
    """在沙盒目录跑可执行命令,带 timeout。不走 shell。"""

    _DESCRIPTION: ClassVar[str] = (
        "Execute a program (not a shell) inside the sandbox working directory. "
        "cmd is a path or command on PATH; args are passed directly as argv. "
        "Returns stdout, stderr, exit code, and a timed_out flag. "
        "Shell features (pipes, redirects, globbing, builtins) are unavailable."
    )

    def __init__(self, name: str, workdir: Path, timeout_s: float) -> None:
        self.name = name
        self.workdir = workdir
        self.timeout_s = timeout_s

    @classmethod
    def create(cls, entry: ToolEntry) -> Self:
        workdir_raw = entry.options.get("workdir", _DEFAULT_WORKDIR)
        timeout_s = entry.options.get("timeout_s", _DEFAULT_TIMEOUT_S)
        if not isinstance(workdir_raw, str) or not workdir_raw:
            raise ConfigError(f"shell_exec.options.workdir 必须是非空字符串,得到 {workdir_raw!r}")
        if not isinstance(timeout_s, int) or timeout_s <= 0:
            raise ConfigError(f"shell_exec.options.timeout_s 必须是正整数,得到 {timeout_s!r}")
        workdir = Path(workdir_raw).expanduser()
        return cls(name=entry.name, workdir=workdir, timeout_s=float(timeout_s))

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self._DESCRIPTION,
            "input_schema": {
                "type": "object",
                "properties": {
                    "cmd": {
                        "type": "string",
                        "description": "Executable path or command on PATH (e.g. 'python', 'git').",
                    },
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Argument list (default []).",
                    },
                },
                "required": ["cmd"],
            },
        }

    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        cmd = input.get("cmd")
        args_raw = input.get("args", [])
        if not isinstance(cmd, str) or not cmd:
            return self._error("input.cmd 必须是非空字符串")
        if not isinstance(args_raw, list) or not all(
            isinstance(a, str) for a in cast(list[Any], args_raw)
        ):
            return self._error("input.args 必须是字符串数组")
        args: list[str] = list(cast(list[str], args_raw))

        try:
            self.workdir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return self._error(f"创建 workdir 失败: {e}")

        try:
            proc = await asyncio.create_subprocess_exec(
                cmd,
                *args,
                cwd=str(self.workdir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (OSError, FileNotFoundError) as e:
            return self._error(f"启动子进程失败: {e}")

        timed_out = False
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            stdout_b, stderr_b = b"", b""
            timed_out = True

        result = {
            "stdout": self._safe_decode(stdout_b),
            "stderr": self._safe_decode(stderr_b),
            "exit_code": proc.returncode,
            "timed_out": timed_out,
        }
        return {
            "type": "tool_result",
            "content": [
                {"type": "text", "text": json.dumps(result, ensure_ascii=False)},
            ],
        }

    @staticmethod
    def _safe_decode(data: bytes) -> str:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("utf-8", errors="replace")

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {
            "type": "tool_result",
            "content": [{"type": "text", "text": message}],
            "is_error": True,
        }

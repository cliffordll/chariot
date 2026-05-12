"""CustomTool —— 自定义工具基类(0.8.7)。

支持两种自定义类型:
- http_custom: 发送 HTTP 请求,变量由 LLM 填充
- shell_custom: 执行预定义命令模板,变量由 LLM 填充

设计约束:
- 不做动态代码生成(script_custom 被禁止)
- 命令/URL 模板预定义,LLM 只能填变量值
- 走现有 guardrails(shell_custom 继承 shell_exec 规则)
"""

from __future__ import annotations

import asyncio
from string import Template
from typing import Any, ClassVar

import httpx

from chariot.agent.exceptions import ConfigError
from chariot.models.tool import ToolEntry
from chariot.tools.base import BaseTool


class CustomTool(BaseTool):
    """自定义工具基类。从 ToolEntry 的 options 中读取配置,动态执行。"""

    _DESCRIPTION: ClassVar[str] = "Custom tool configured by user."

    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description

    @classmethod
    def create(cls, entry: ToolEntry) -> BaseTool:
        if entry.custom_type == "http_custom":
            return HttpCustomTool.create(entry)
        if entry.custom_type == "shell_custom":
            return ShellCustomTool.create(entry)
        raise ConfigError(f"未知 custom_type: {entry.custom_type!r}")

    def schema(self) -> dict[str, Any]:
        # 子类 override
        raise NotImplementedError

    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        # 子类 override
        raise NotImplementedError


class HttpCustomTool(CustomTool):
    """HTTP 自定义工具。

    options 示例:
        {
            "method": "GET",
            "url_template": "https://api.example.com/items/${id}",
            "headers": {"Authorization": "Bearer ${token}"},
            "body_template": null,
            "timeout_s": 10,
            "max_bytes": 102400,
            "allowed_domains": ["api.example.com"]
        }
    """

    def __init__(
        self,
        name: str,
        description: str,
        method: str,
        url_template: str,
        headers: dict[str, str],
        body_template: str | None,
        timeout_s: float,
        max_bytes: int,
        allowed_domains: tuple[str, ...] | None,
    ) -> None:
        super().__init__(name, description)
        self.method = method.upper()
        self.url_template = url_template
        self.headers = headers
        self.body_template = body_template
        self.timeout_s = timeout_s
        self.max_bytes = max_bytes
        self.allowed_domains = allowed_domains

    @classmethod
    def create(cls, entry: ToolEntry) -> BaseTool:
        opts = entry.options
        method = opts.get("method", "GET")
        url_template = opts.get("url_template", "")
        headers = opts.get("headers", {})
        body_template = opts.get("body_template")
        timeout_s = opts.get("timeout_s", 10.0)
        max_bytes = opts.get("max_bytes", 102400)
        allowed_raw = opts.get("allowed_domains")

        if not isinstance(method, str) or method not in ("GET", "POST", "PUT", "DELETE", "PATCH"):
            raise ConfigError(f"http_custom.method 必须是 GET/POST/PUT/DELETE/PATCH,得到 {method!r}")
        if not isinstance(url_template, str) or not url_template:
            raise ConfigError("http_custom.url_template 必须是非空字符串")
        if not isinstance(headers, dict):
            raise ConfigError("http_custom.headers 必须是 dict")
        if body_template is not None and not isinstance(body_template, str):
            raise ConfigError("http_custom.body_template 必须是字符串或 null")

        allowed = None
        if allowed_raw is not None:
            if not isinstance(allowed_raw, list) or not all(isinstance(d, str) for d in allowed_raw):
                raise ConfigError("http_custom.allowed_domains 必须是字符串列表或 null")
            allowed = tuple(d.lower() for d in allowed_raw)

        return cls(
            name=entry.name,
            description=entry.description or f"HTTP {method} {url_template}",
            method=method,
            url_template=url_template,
            headers=headers,
            body_template=body_template,
            timeout_s=float(timeout_s),
            max_bytes=int(max_bytes),
            allowed_domains=allowed,
        )

    def schema(self) -> dict[str, Any]:
        # 从 url_template 和 headers 中提取变量名
        variables = self._extract_variables()
        properties: dict[str, Any] = {
            v: {"type": "string", "description": f"Value for template variable '{v}'"} for v in variables
        }
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": list(variables),
            },
        }

    def _extract_variables(self) -> set[str]:
        """从 url_template / headers / body_template 中提取 ${var} 变量名。"""
        import re

        variables: set[str] = set()
        for template_str in [self.url_template, self.body_template or "", *self.headers.values()]:
            for match in re.finditer(r"\$\{([^}]+)\}", template_str):
                variables.add(match.group(1))
        return variables

    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        # 填充模板
        try:
            url = Template(self.url_template).substitute(input)
            headers = {k: Template(v).substitute(input) for k, v in self.headers.items()}
            body = Template(self.body_template).substitute(input) if self.body_template else None
        except KeyError as e:
            return self._error(f"模板变量缺失: {e};需要变量: {self._extract_variables()}")
        except ValueError as e:
            return self._error(f"模板填充失败: {e}")

        # 域名检查
        from urllib.parse import urlparse

        domain = (urlparse(url).netloc or "").lower()
        if self.allowed_domains is not None and domain not in self.allowed_domains:
            return self._error(f"域名 {domain!r} 不在 allowed_domains 中")

        # 发送请求
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=True) as client:
                response = await client.request(
                    self.method,
                    url,
                    headers=headers,
                    content=body.encode("utf-8") if body else None,
                )
                response.raise_for_status()
                text = response.text
        except httpx.HTTPStatusError as e:
            return self._error(f"HTTP {e.response.status_code}: {e.response.text[:500]}")
        except httpx.RequestError as e:
            return self._error(f"请求失败: {e}")
        except Exception as e:
            return self._error(f"HTTP 工具执行失败: {e}")

        if len(text) > self.max_bytes:
            text = text[: self.max_bytes] + "\n\n... [truncated]"

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


class ShellCustomTool(CustomTool):
    """Shell 自定义工具。

    options 示例:
        {
            "command_template": "kubectl get pods -n ${namespace}",
            "workdir": "/app",
            "timeout_s": 30
        }
    """

    def __init__(
        self,
        name: str,
        description: str,
        command_template: str,
        workdir: str,
        timeout_s: float,
    ) -> None:
        super().__init__(name, description)
        self.command_template = command_template
        self.workdir = workdir
        self.timeout_s = timeout_s

    @classmethod
    def create(cls, entry: ToolEntry) -> BaseTool:
        opts = entry.options
        command_template = opts.get("command_template", "")
        workdir = opts.get("workdir", ".")
        timeout_s = opts.get("timeout_s", 30.0)

        if not isinstance(command_template, str) or not command_template:
            raise ConfigError("shell_custom.command_template 必须是非空字符串")
        if not isinstance(workdir, str):
            raise ConfigError("shell_custom.workdir 必须是字符串")

        return cls(
            name=entry.name,
            description=entry.description or f"Shell: {command_template}",
            command_template=command_template,
            workdir=workdir,
            timeout_s=float(timeout_s),
        )

    def schema(self) -> dict[str, Any]:
        variables = self._extract_variables()
        properties: dict[str, Any] = {
            v: {"type": "string", "description": f"Value for template variable '{v}'"} for v in variables
        }
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": list(variables),
            },
        }

    def _extract_variables(self) -> set[str]:
        import re

        variables: set[str] = set()
        for match in re.finditer(r"\$\{([^}]+)\}", self.command_template):
            variables.add(match.group(1))
        return variables

    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        try:
            command = Template(self.command_template).substitute(input)
        except KeyError as e:
            return self._error(f"模板变量缺失: {e};需要变量: {self._extract_variables()}")
        except ValueError as e:
            return self._error(f"模板填充失败: {e}")

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=self.workdir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.timeout_s,
            )
        except TimeoutError:
            return self._error(f"命令超时(>{self.timeout_s}s): {command}")
        except Exception as e:
            return self._error(f"命令执行失败: {e}")

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")

        output = f"$ {command}\n\n{stdout_text}"
        if stderr_text:
            output += f"\n[stderr]\n{stderr_text}"

        if proc.returncode != 0:
            return {
                "type": "tool_result",
                "content": [{"type": "text", "text": output}],
                "is_error": True,
            }

        return {
            "type": "tool_result",
            "content": [{"type": "text", "text": output}],
        }

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {
            "type": "tool_result",
            "content": [{"type": "text", "text": message}],
            "is_error": True,
        }

"""Tool 层测试 —— ToolRegistry + 4 内置工具的 happy path + 边界。

shell_exec / http_get 的真实子进程 / 网络访问会被相应替代:
- shell_exec:跑 `python -c "..."` 真子进程(测试机有 python),timeout 用 0.5s
  + sleep 1s 验 timed_out
- http_get:用 `httpx.MockTransport` 拦截请求,monkeypatch 实例的 `_make_client`
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Self

import httpx
import pytest

from chariot.server.config import ConfigError, ToolEntry
from chariot.server.tool.base import Tool
from chariot.server.tool.httpget import HttpGetTool
from chariot.server.tool.listdir import ListDirTool
from chariot.server.tool.readfile import ReadFileTool
from chariot.server.tool.registry import ToolRegistry
from chariot.server.tool.shellexec import ShellExecTool

# ============================================================
# ToolRegistry
# ============================================================


class _StubTool(Tool):
    """测试用 tool;最小满足 Tool ABC 契约。"""

    def __init__(self, name: str, options: dict[str, Any]) -> None:
        self.name = name
        self.options = options

    @classmethod
    def from_config(cls, entry: ToolEntry) -> Self:
        return cls(name=entry.name, options=entry.options)

    def schema(self) -> dict[str, Any]:
        return {"name": self.name, "description": "stub", "input_schema": {}}

    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover
        del input
        return {"type": "tool_result", "content": []}


def test_register_and_build_returns_tool_instance() -> None:
    ToolRegistry.register("stub", _StubTool)
    entry = ToolEntry(name="x", type="stub", enabled=True, options={"foo": "bar"})
    inst = ToolRegistry.build(entry)
    assert isinstance(inst, _StubTool)
    assert inst.name == "x"
    assert inst.options == {"foo": "bar"}


def test_known_types_lists_registered() -> None:
    ToolRegistry.register("aaa", _StubTool)
    ToolRegistry.register("bbb", _StubTool)
    types = ToolRegistry.known_types()
    assert "aaa" in types
    assert "bbb" in types
    assert types == sorted(types)


def test_register_duplicate_type_raises() -> None:
    ToolRegistry.register("dup", _StubTool)
    with pytest.raises(ValueError, match="重复"):
        ToolRegistry.register("dup", _StubTool)


def test_build_unknown_type_raises_config_error() -> None:
    entry = ToolEntry(name="x", type="ghost", enabled=True, options={})
    with pytest.raises(ConfigError, match="ghost"):
        ToolRegistry.build(entry)


def test_builtins_registered_via_package_init() -> None:
    """`chariot.server.tool.__init__` 显式注册 4 个内置工具,autouse fixture
    的 snapshot 应包含它们。"""
    types = ToolRegistry.known_types()
    assert "read_file" in types
    assert "list_dir" in types
    assert "shell_exec" in types
    assert "http_get" in types


# ============================================================
# ReadFileTool
# ============================================================


def _make_read_file(max_bytes: int = 1048576) -> ReadFileTool:
    return ReadFileTool.from_config(
        ToolEntry(
            name="read_file",
            type="read_file",
            enabled=True,
            options={"max_bytes": max_bytes},
        ),
    )


async def test_read_file_happy(tmp_path: Path) -> None:
    p = tmp_path / "hello.txt"
    p.write_text("hello world", encoding="utf-8")
    result = await _make_read_file().execute({"path": str(p)})
    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    assert text == "hello world"


async def test_read_file_path_missing_input_field() -> None:
    result = await _make_read_file().execute({})
    assert result["is_error"] is True
    assert "path" in result["content"][0]["text"]


async def test_read_file_not_exists(tmp_path: Path) -> None:
    result = await _make_read_file().execute({"path": str(tmp_path / "ghost.txt")})
    assert result["is_error"] is True
    assert "不存在" in result["content"][0]["text"]


async def test_read_file_is_directory_rejected(tmp_path: Path) -> None:
    result = await _make_read_file().execute({"path": str(tmp_path)})
    assert result["is_error"] is True
    assert "不是文件" in result["content"][0]["text"]


async def test_read_file_truncated_marker(tmp_path: Path) -> None:
    """超过 max_bytes 时只返前 max_bytes,文本末加 truncated 标记。"""
    p = tmp_path / "big.txt"
    p.write_bytes(b"a" * 100)
    tool = _make_read_file(max_bytes=10)
    result = await tool.execute({"path": str(p)})
    text = result["content"][0]["text"]
    assert text.startswith("a" * 10)
    assert "truncated" in text
    assert "total=100" in text


async def test_read_file_binary_falls_back_to_base64(tmp_path: Path) -> None:
    p = tmp_path / "blob.bin"
    p.write_bytes(b"\xff\xfe\x00\x01\x02")  # 非 UTF-8
    result = await _make_read_file().execute({"path": str(p)})
    text = result["content"][0]["text"]
    assert text.startswith("[base64] ")


def test_read_file_schema_has_path_required() -> None:
    schema = _make_read_file().schema()
    assert schema["name"] == "read_file"
    assert "path" in schema["input_schema"]["required"]


def test_read_file_invalid_max_bytes_raises() -> None:
    with pytest.raises(ConfigError, match="max_bytes"):
        ReadFileTool.from_config(
            ToolEntry(
                name="read_file",
                type="read_file",
                enabled=True,
                options={"max_bytes": -1},
            ),
        )


# ============================================================
# ListDirTool
# ============================================================


def _make_list_dir() -> ListDirTool:
    return ListDirTool.from_config(
        ToolEntry(name="list_dir", type="list_dir", enabled=True, options={}),
    )


async def test_list_dir_happy(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("bb")
    (tmp_path / "sub").mkdir()
    result = await _make_list_dir().execute({"path": str(tmp_path)})
    assert result.get("is_error") is not True
    items = json.loads(result["content"][0]["text"])
    names = {it["name"] for it in items}
    assert names == {"a.txt", "b.txt", "sub"}
    by_name = {it["name"]: it for it in items}
    assert by_name["a.txt"]["type"] == "file"
    assert by_name["a.txt"]["size"] == 1
    assert by_name["b.txt"]["size"] == 2
    assert by_name["sub"]["type"] == "dir"
    # dir 排在 file 前
    assert items[0]["type"] == "dir"


async def test_list_dir_recursive(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("b")
    result = await _make_list_dir().execute(
        {"path": str(tmp_path), "recursive": True},
    )
    items = json.loads(result["content"][0]["text"])
    names = {it["name"] for it in items}
    # recursive 模式 name 是相对 root 的路径
    assert "a.txt" in names
    assert "sub" in names
    assert any(n.endswith("b.txt") for n in names)


async def test_list_dir_not_exists(tmp_path: Path) -> None:
    result = await _make_list_dir().execute({"path": str(tmp_path / "ghost")})
    assert result["is_error"] is True


async def test_list_dir_is_file_rejected(tmp_path: Path) -> None:
    p = tmp_path / "f.txt"
    p.write_text("x")
    result = await _make_list_dir().execute({"path": str(p)})
    assert result["is_error"] is True


def test_list_dir_schema_has_path_required() -> None:
    schema = _make_list_dir().schema()
    assert schema["name"] == "list_dir"
    assert "path" in schema["input_schema"]["required"]
    assert "recursive" in schema["input_schema"]["properties"]


# ============================================================
# ShellExecTool
# ============================================================


def _make_shell(workdir: Path, timeout_s: float = 5.0) -> ShellExecTool:
    """直接构造,绕开 from_config 的 int 校验(测试用 sub-second timeout 方便)。"""
    return ShellExecTool(name="shell_exec", workdir=workdir, timeout_s=timeout_s)


async def test_shell_exec_happy_python(tmp_path: Path) -> None:
    """跑 python -c 'print("hello")' 真子进程,验 stdout / exit_code。"""
    import sys

    tool = _make_shell(workdir=tmp_path)
    result = await tool.execute({"cmd": sys.executable, "args": ["-c", "print('hello')"]})
    assert result.get("is_error") is not True
    payload = json.loads(result["content"][0]["text"])
    assert "hello" in payload["stdout"]
    assert payload["exit_code"] == 0
    assert payload["timed_out"] is False


async def test_shell_exec_timeout(tmp_path: Path) -> None:
    """timeout 触发,子进程被 kill,timed_out=True。"""
    import sys

    tool = ShellExecTool(name="shell_exec", workdir=tmp_path, timeout_s=0.5)
    result = await tool.execute(
        {
            "cmd": sys.executable,
            "args": ["-c", "import time; time.sleep(3)"],
        },
    )
    payload = json.loads(result["content"][0]["text"])
    assert payload["timed_out"] is True


async def test_shell_exec_workdir_auto_mkdir(tmp_path: Path) -> None:
    """workdir 不存在时 execute 自动 mkdir,不报错。"""
    import sys

    target = tmp_path / "newly_created"
    assert not target.exists()
    tool = _make_shell(workdir=target)
    result = await tool.execute({"cmd": sys.executable, "args": ["-c", "pass"]})
    assert result.get("is_error") is not True
    assert target.exists()


async def test_shell_exec_invalid_cmd(tmp_path: Path) -> None:
    """空 cmd 走输入校验路径(不实际执行)。"""
    tool = _make_shell(workdir=tmp_path)
    result = await tool.execute({"cmd": ""})
    assert result["is_error"] is True


async def test_shell_exec_invalid_args(tmp_path: Path) -> None:
    """args 不是 list[str] 走校验路径。"""
    tool = _make_shell(workdir=tmp_path)
    result = await tool.execute({"cmd": "ls", "args": "not-a-list"})
    assert result["is_error"] is True


def test_shell_exec_invalid_options_workdir() -> None:
    with pytest.raises(ConfigError, match="workdir"):
        ShellExecTool.from_config(
            ToolEntry(
                name="shell_exec",
                type="shell_exec",
                enabled=True,
                options={"workdir": ""},
            ),
        )


def test_shell_exec_invalid_options_timeout() -> None:
    with pytest.raises(ConfigError, match="timeout"):
        ShellExecTool.from_config(
            ToolEntry(
                name="shell_exec",
                type="shell_exec",
                enabled=True,
                options={"workdir": "/tmp", "timeout_s": 0},
            ),
        )


def test_shell_exec_schema_has_cmd_required() -> None:
    schema = _make_shell(workdir=Path(".")).schema()
    assert schema["name"] == "shell_exec"
    assert "cmd" in schema["input_schema"]["required"]


# ============================================================
# HttpGetTool
# ============================================================


def _make_http(allowed: list[str], max_bytes: int = 524288) -> HttpGetTool:
    return HttpGetTool.from_config(
        ToolEntry(
            name="http_get",
            type="http_get",
            enabled=True,
            options={"allowed_domains": allowed, "max_bytes": max_bytes},
        ),
    )


def _inject_mock_transport(
    tool: HttpGetTool,
    handler: Any,  # callable httpx.Request -> httpx.Response
) -> None:
    """让 tool 实例的 _make_client 返回带 MockTransport 的 client。"""
    transport = httpx.MockTransport(handler)
    tool._make_client = lambda: httpx.AsyncClient(transport=transport)  # type: ignore[method-assign]


async def test_http_get_rejected_when_not_in_allowlist() -> None:
    tool = _make_http(allowed=["api.github.com"])
    result = await tool.execute({"url": "https://evil.example.com/data"})
    assert result["is_error"] is True
    assert "白名单" in result["content"][0]["text"]


async def test_http_get_empty_allowlist_rejects_all() -> None:
    tool = _make_http(allowed=[])
    result = await tool.execute({"url": "https://api.github.com/"})
    assert result["is_error"] is True


async def test_http_get_happy_path() -> None:
    tool = _make_http(allowed=["api.github.com"])

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'{"ok": true}',
            headers={"content-type": "application/json"},
        )

    _inject_mock_transport(tool, _handler)
    result = await tool.execute({"url": "https://api.github.com/repos"})
    assert result.get("is_error") is not True
    payload = json.loads(result["content"][0]["text"])
    assert payload["status"] == 200
    assert payload["body"] == '{"ok": true}'
    assert payload["headers"]["content-type"] == "application/json"
    assert payload["truncated"] is False


async def test_http_get_truncates_large_body() -> None:
    tool = _make_http(allowed=["api.example.com"], max_bytes=5)

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"abcdefghij")

    _inject_mock_transport(tool, _handler)
    result = await tool.execute({"url": "https://api.example.com/big"})
    payload = json.loads(result["content"][0]["text"])
    assert payload["body"] == "abcde"
    assert payload["truncated"] is True


async def test_http_get_binary_response_base64() -> None:
    tool = _make_http(allowed=["api.example.com"])

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\xff\xfe\x00\x01")

    _inject_mock_transport(tool, _handler)
    result = await tool.execute({"url": "https://api.example.com/blob"})
    payload = json.loads(result["content"][0]["text"])
    assert payload["body"].startswith("[base64] ")


async def test_http_get_passes_headers_through() -> None:
    """input.headers 应原样传给上游(测试 mock 收到了)。"""
    tool = _make_http(allowed=["api.example.com"])
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["x-custom"] = request.headers.get("x-custom", "")
        return httpx.Response(200, content=b"ok")

    _inject_mock_transport(tool, _handler)
    await tool.execute(
        {"url": "https://api.example.com/", "headers": {"x-custom": "v1"}},
    )
    assert captured["x-custom"] == "v1"


async def test_http_get_invalid_scheme() -> None:
    tool = _make_http(allowed=["api.example.com"])
    result = await tool.execute({"url": "ftp://api.example.com/x"})
    assert result["is_error"] is True
    assert "scheme" in result["content"][0]["text"]


async def test_http_get_missing_url_field() -> None:
    tool = _make_http(allowed=["api.example.com"])
    result = await tool.execute({})
    assert result["is_error"] is True


async def test_http_get_invalid_options_allowed_domains() -> None:
    with pytest.raises(ConfigError, match="allowed_domains"):
        HttpGetTool.from_config(
            ToolEntry(
                name="http_get",
                type="http_get",
                enabled=True,
                options={"allowed_domains": "not-a-list"},
            ),
        )


def test_http_get_schema_has_url_required() -> None:
    schema = _make_http(allowed=[]).schema()
    assert schema["name"] == "http_get"
    assert "url" in schema["input_schema"]["required"]

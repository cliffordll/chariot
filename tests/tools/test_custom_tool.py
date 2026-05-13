"""Tests for CustomTool / HttpCustomTool / ShellCustomTool."""

from __future__ import annotations

import pytest

from chariot.agent.exceptions import ConfigError
from chariot.models.tool import ToolEntry
from chariot.tools.custom import CustomTool, HttpCustomTool, ShellCustomTool


def _make_http(**opts: object) -> HttpCustomTool:
    return HttpCustomTool.create(
        ToolEntry(
            name="my_api",
            type="http_custom",
            enabled=True,
            options=opts,
            source="custom",
            custom_type="http_custom",
        )
    )


def _make_shell(**opts: object) -> ShellCustomTool:
    return ShellCustomTool.create(
        ToolEntry(
            name="my_cmd",
            type="shell_custom",
            enabled=True,
            options=opts,
            source="custom",
            custom_type="shell_custom",
        )
    )


# ---- HttpCustomTool ----


def test_http_custom_create_basic() -> None:
    tool = _make_http(
        method="GET",
        url_template="https://api.example.com/items/${id}",
        headers={"Authorization": "Bearer ${token}"},
    )
    assert tool.name == "my_api"
    assert tool.method == "GET"


def test_http_custom_invalid_method() -> None:
    with pytest.raises(ConfigError):
        _make_http(method="INVALID")


def test_http_custom_schema_extracts_variables() -> None:
    tool = _make_http(
        url_template="https://api.example.com/${resource}/${id}",
        headers={"X-Key": "${key}"},
    )
    schema = tool.schema()
    props = schema["input_schema"]["properties"]
    assert "resource" in props
    assert "id" in props
    assert "key" in props


@pytest.mark.asyncio
async def test_http_custom_execute_missing_variable() -> None:
    tool = _make_http(url_template="https://api.example.com/${id}")
    result = await tool.execute({})
    assert result.get("is_error")
    assert "模板变量缺失" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_http_custom_domain_not_allowed() -> None:
    tool = _make_http(
        url_template="https://other.com/${path}",
        allowed_domains=["example.com"],
    )
    result = await tool.execute({"path": "test"})
    assert result.get("is_error")
    assert "不在 allowed_domains" in result["content"][0]["text"]


# ---- ShellCustomTool ----


def test_shell_custom_create_basic() -> None:
    tool = _make_shell(command_template="echo ${msg}")
    assert tool.name == "my_cmd"
    assert tool.command_template == "echo ${msg}"


def test_shell_custom_invalid_template() -> None:
    with pytest.raises(ConfigError):
        _make_shell(command_template="")


def test_shell_custom_schema_extracts_variables() -> None:
    tool = _make_shell(command_template="kubectl get pods -n ${namespace} --label=${label}")
    schema = tool.schema()
    props = schema["input_schema"]["properties"]
    assert "namespace" in props
    assert "label" in props


@pytest.mark.asyncio
async def test_shell_custom_execute_missing_variable() -> None:
    tool = _make_shell(command_template="echo ${msg}")
    result = await tool.execute({})
    assert result.get("is_error")
    assert "模板变量缺失" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_shell_custom_execute_echo() -> None:
    tool = _make_shell(command_template="echo ${msg}")
    result = await tool.execute({"msg": "hello"})
    assert not result.get("is_error")
    assert "hello" in result["content"][0]["text"]


# ---- CustomTool dispatch ----


def test_custom_tool_dispatch_http() -> None:
    entry = ToolEntry(
        name="x",
        type="http_custom",
        enabled=True,
        options={"url_template": "https://a.com"},
        source="custom",
        custom_type="http_custom",
    )
    tool = CustomTool.create(entry)
    assert isinstance(tool, HttpCustomTool)


def test_custom_tool_dispatch_shell() -> None:
    entry = ToolEntry(
        name="x",
        type="shell_custom",
        enabled=True,
        options={"command_template": "echo hi"},
        source="custom",
        custom_type="shell_custom",
    )
    tool = CustomTool.create(entry)
    assert isinstance(tool, ShellCustomTool)


def test_custom_tool_dispatch_unknown() -> None:
    entry = ToolEntry(
        name="x",
        type="custom",
        enabled=True,
        options={},
        source="custom",
        custom_type="invalid",
    )
    with pytest.raises(ConfigError):
        CustomTool.create(entry)

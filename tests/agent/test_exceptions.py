"""AIAgent 内核异常单测。

覆盖:
- ProviderError 三元组(code / message / status)+ 默认 status=502
- ConfigError 子类层级
- ConvoLockTimeout 的 layer 字段
- ToolExecutionError 的 tool_name 字段
"""

from __future__ import annotations

import pytest

from chariot.agent.exceptions import (
    ConfigError,
    ConvoLockTimeout,
    ConvoNotFound,
    DuplicateConvoId,
    DuplicateProviderName,
    ProviderError,
    ProviderNotFound,
    ToolExecutionError,
    ToolNotFound,
)


class TestProviderError:
    def test_default_status(self) -> None:
        exc = ProviderError("upstream_auth_failed", "401 unauthorized")
        assert exc.code == "upstream_auth_failed"
        assert exc.message == "401 unauthorized"
        assert exc.status == 502
        assert str(exc) == "401 unauthorized"

    def test_explicit_status(self) -> None:
        exc = ProviderError("rate_limited", "too many", status=429)
        assert exc.status == 429

    def test_is_exception(self) -> None:
        with pytest.raises(ProviderError) as exc_info:
            raise ProviderError("upstream_unreachable", "connect timeout")
        assert exc_info.value.code == "upstream_unreachable"


class TestConfigError:
    def test_base(self) -> None:
        exc = ConfigError("invalid section")
        assert isinstance(exc, Exception)

    def test_provider_not_found_subclass(self) -> None:
        exc = ProviderNotFound("model x not in db")
        assert isinstance(exc, ConfigError)

    def test_duplicate_model_name_subclass(self) -> None:
        exc = DuplicateProviderName("name claude already exists")
        assert isinstance(exc, ConfigError)

    def test_tool_not_found_subclass(self) -> None:
        exc = ToolNotFound("tool x not in db")
        assert isinstance(exc, ConfigError)

    def test_convo_not_found_subclass(self) -> None:
        exc = ConvoNotFound("conv 01H... not in db")
        assert isinstance(exc, ConfigError)

    def test_duplicate_convo_id_subclass(self) -> None:
        exc = DuplicateConvoId("conv id already used")
        assert isinstance(exc, ConfigError)


class TestToolExecutionError:
    def test_default_no_tool_name(self) -> None:
        exc = ToolExecutionError("file not found")
        assert exc.message == "file not found"
        assert exc.tool_name is None
        assert exc.extra == {}

    def test_with_tool_name(self) -> None:
        exc = ToolExecutionError("permission denied", tool_name="read_file")
        assert exc.tool_name == "read_file"

    def test_with_extra(self) -> None:
        exc = ToolExecutionError("bad input", tool_name="shell_exec", path="/etc/passwd")
        assert exc.extra == {"path": "/etc/passwd"}


class TestConvoLockTimeout:
    def test_local_layer(self) -> None:
        exc = ConvoLockTimeout("waited 30s", layer="local")
        assert exc.layer == "local"
        assert exc.message == "waited 30s"

    def test_db_layer(self) -> None:
        exc = ConvoLockTimeout("retried 3 times", layer="db")
        assert exc.layer == "db"

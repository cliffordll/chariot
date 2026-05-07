"""ProviderRegistry 单测。

覆盖:
- `register` / `build` / `known_types` / `unregister` 基本契约
- 重复注册同 type_name 抛 ValueError
- 未知 type_name `build` 抛 ConfigError
- `BaseProvider` 是 ABC(不能直接实例化)

注:`chariot.providers.__init__` 导入时会自动 `register("mock", ...)`,
所以测试用 setup/teardown 把注册表 reset 成可控状态。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Self

import pytest

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest
from chariot.agent.exceptions import ConfigError
from chariot.providers.base import BaseProvider, BaseProviderConfig
from chariot.providers.registry import ProviderRegistry


class _DummyProvider(BaseProvider):
    """测试专用 Provider 实现(不在 chariot/providers/builtin/,只在测试可见)。"""

    def __init__(self, config: BaseProviderConfig) -> None:
        self.config = config

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> Self:
        del options
        return cls(config=BaseProviderConfig(name="dummy", model="dummy-1"))

    async def generate(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        del req
        yield ChatEvent.message_done()


class TestProviderRegistryBasic:
    """register / build / known_types 契约。"""

    def setup_method(self) -> None:
        """每个 test 起前清空注册表(隔离 import 时的自动注册)。"""
        ProviderRegistry._reset()

    def teardown_method(self) -> None:
        """每个 test 收尾后恢复 import 时的注册(避免污染后续测试模块)。"""
        ProviderRegistry._reset()
        from chariot.providers.builtin.mock import MockProvider

        ProviderRegistry.register("mock", MockProvider)

    def test_register_and_build(self) -> None:
        ProviderRegistry.register("dummy", _DummyProvider)
        provider = ProviderRegistry.build("dummy", {})
        assert isinstance(provider, _DummyProvider)
        assert provider.config.name == "dummy"

    def test_known_types(self) -> None:
        ProviderRegistry.register("dummy", _DummyProvider)
        ProviderRegistry.register("dummy2", _DummyProvider)
        assert ProviderRegistry.known_types() == {"dummy", "dummy2"}

    def test_duplicate_register_raises(self) -> None:
        ProviderRegistry.register("dummy", _DummyProvider)
        with pytest.raises(ValueError, match="duplicate provider type: dummy"):
            ProviderRegistry.register("dummy", _DummyProvider)

    def test_build_unknown_type_raises_config_error(self) -> None:
        with pytest.raises(ConfigError, match="unknown provider type"):
            ProviderRegistry.build("not_registered", {})

    def test_unregister(self) -> None:
        ProviderRegistry.register("dummy", _DummyProvider)
        ProviderRegistry.unregister("dummy")
        assert "dummy" not in ProviderRegistry.known_types()

    def test_unregister_nonexistent_idempotent(self) -> None:
        """unregister 不存在的 type_name 不报错(idempotent)。"""
        ProviderRegistry.unregister("never_registered")  # 不抛


class TestBaseProviderIsAbstract:
    """BaseProvider 是 ABC,不能直接实例化。"""

    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            BaseProvider()  # type: ignore[abstract]

    def test_abstract_methods(self) -> None:
        """`from_options` / `generate` 都标了 abstract。"""
        assert "from_options" in BaseProvider.__abstractmethods__
        assert "generate" in BaseProvider.__abstractmethods__


class TestImportTimeRegistration:
    """`import chariot.providers` 触发 MockProvider 自动注册。"""

    def test_mock_registered_on_import(self) -> None:
        # 触发 import(可能已 import,但确保 known_types 反映已注册状态)
        import chariot.providers  # noqa: F401

        assert "mock" in ProviderRegistry.known_types()

"""ModelRegistry 测试 —— 注册 / 构造 / 错误路径 / 已知 type。

注册表是类级单例(ClassVar)。conftest 的 `isolate_model_registry` autouse fixture
负责每个用例前后 snapshot/restore,所以本文件的用例可以放心改 `_builders`,不会
污染其它用例(包括默认注册的 "mock" / "anthropic")。
"""

from __future__ import annotations

from typing import Any, Self

import pytest
from fastapi.responses import Response

from chariot.agent.config import ConfigError, ProviderEntry
from chariot.server.model.base import Model
from chariot.server.model.registry import ModelRegistry


class _StubModel(Model):
    """测试用 model;最小满足 Model ABC 契约。"""

    name = "stub"

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        self.options = options or {}

    @classmethod
    def from_config(cls, options: dict[str, Any]) -> Self:
        return cls(options=options)

    async def respond(self, body: bytes, *, stream: bool) -> Response:  # pragma: no cover
        del body, stream
        return Response(content=b"", status_code=200)


# ---------- 注册 + 构造正常路径 ----------


def test_register_and_build_returns_model_instance() -> None:
    ModelRegistry.register("stub", _StubModel)
    entry = ProviderEntry(name="x", type="stub", options={"foo": "bar"})
    inst = ModelRegistry.build(entry)
    assert isinstance(inst, _StubModel)
    assert inst.options == {"foo": "bar"}


def test_known_types_lists_registered() -> None:
    ModelRegistry.register("aaa", _StubModel)
    ModelRegistry.register("bbb", _StubModel)
    types = ModelRegistry.known_types()
    assert "aaa" in types
    assert "bbb" in types
    assert types == sorted(types)  # known_types 应排序


# ---------- 错误路径 ----------


def test_register_duplicate_type_raises() -> None:
    ModelRegistry.register("dup", _StubModel)
    with pytest.raises(ValueError, match="重复"):
        ModelRegistry.register("dup", _StubModel)


def test_subclass_missing_abstractmethod_cant_instantiate() -> None:
    """Model 是 ABC,子类必须实现 from_config / respond,否则实例化即 TypeError。
    register 不再做运行期校验,契约由 ABC 承载。"""

    class _Incomplete(Model):
        name = "x"
        # 故意不实现 from_config / respond

    with pytest.raises(TypeError, match="abstract"):
        _Incomplete()  # type: ignore[abstract]


def test_build_unknown_type_raises_config_error() -> None:
    entry = ProviderEntry(name="x", type="ghost", options={})
    with pytest.raises(ConfigError, match="ghost"):
        ModelRegistry.build(entry)


def test_build_unknown_type_message_lists_known_types() -> None:
    """错误消息把已注册的 type 列出来,方便排错。"""
    ModelRegistry.register("alpha", _StubModel)
    entry = ProviderEntry(name="x", type="ghost", options={})
    with pytest.raises(ConfigError, match="alpha"):
        ModelRegistry.build(entry)


# ---------- 内置 model 注册检验 ----------


def test_mock_model_registered_via_package_init() -> None:
    """`chariot.server.model.__init__` 显式注册 'mock' / 'anthropic',autouse
    fixture 的 snapshot 应包含它们。"""
    from chariot.server.model.mock import MockModel

    assert "mock" in ModelRegistry.known_types()
    entry = ProviderEntry(name="default", type="mock", options={})
    inst = ModelRegistry.build(entry)
    assert isinstance(inst, MockModel)

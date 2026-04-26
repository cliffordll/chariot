"""ModelRegistry 测试 —— 注册 / 构造 / 错误路径 / 已知 type。

注册表是类级单例(ClassVar)。每个用例用 `registry_isolation` fixture 在 setup
里 snapshot,teardown 里 restore,避免污染其它用例(尤其是已注册的 "mock")。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.responses import Response

from chariot.server.config import ConfigError, ModelEntry
from chariot.server.model.registry import ModelRegistry


@pytest.fixture
def registry_isolation() -> Iterator[None]:
    snap = ModelRegistry.snapshot()
    yield
    ModelRegistry.restore(snap)


class _StubModel:
    """测试用 model;最小满足 Model 协议 + from_config 契约。"""

    name = "stub"

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        self.options = options or {}

    @classmethod
    def from_config(cls, options: dict[str, Any]) -> _StubModel:
        return cls(options=options)

    async def respond(self, body: bytes, *, stream: bool) -> Response:  # pragma: no cover
        del body, stream
        return Response(content=b"", status_code=200)


# ---------- 注册 + 构造正常路径 ----------


def test_register_and_build_returns_model_instance(registry_isolation: None) -> None:
    ModelRegistry.register("stub")(_StubModel)
    entry = ModelEntry(name="x", type="stub", options={"foo": "bar"})
    inst = ModelRegistry.build(entry)
    assert isinstance(inst, _StubModel)
    assert inst.options == {"foo": "bar"}


def test_known_types_lists_registered(registry_isolation: None) -> None:
    ModelRegistry.register("aaa")(_StubModel)
    ModelRegistry.register("bbb")(_StubModel)
    types = ModelRegistry.known_types()
    assert "aaa" in types
    assert "bbb" in types
    assert types == sorted(types)  # known_types 应排序


# ---------- 错误路径 ----------


def test_register_duplicate_type_raises(registry_isolation: None) -> None:
    ModelRegistry.register("dup")(_StubModel)
    with pytest.raises(ValueError, match="重复"):
        ModelRegistry.register("dup")(_StubModel)


def test_register_class_without_from_config_fails(registry_isolation: None) -> None:
    class _NoFromConfig:
        name = "no"

    with pytest.raises(TypeError, match="from_config"):
        ModelRegistry.register("no_fc")(_NoFromConfig)  # type: ignore[arg-type]


def test_build_unknown_type_raises_config_error(registry_isolation: None) -> None:
    entry = ModelEntry(name="x", type="ghost", options={})
    with pytest.raises(ConfigError, match="ghost"):
        ModelRegistry.build(entry)


def test_build_unknown_type_message_lists_known_types(registry_isolation: None) -> None:
    """错误消息把已注册的 type 列出来,方便排错。"""
    ModelRegistry.register("alpha")(_StubModel)
    entry = ModelEntry(name="x", type="ghost", options={})
    with pytest.raises(ConfigError, match="alpha"):
        ModelRegistry.build(entry)


# ---------- mock 模块 import 的副作用 ----------


def test_mock_model_registered_on_module_import() -> None:
    """`chariot.server.model.mock` 已在 conftest 阶段被加载,'mock' type 应可用。

    本用例不用 registry_isolation —— 故意验真实注册状态。
    """
    from chariot.server.model.mock import MockModel

    assert "mock" in ModelRegistry.known_types()
    entry = ModelEntry(name="default", type="mock", options={})
    inst = ModelRegistry.build(entry)
    assert isinstance(inst, MockModel)


# ---------- snapshot / restore 自身 ----------


def test_snapshot_restore_round_trip(registry_isolation: None) -> None:
    ModelRegistry.register("temp")(_StubModel)
    assert "temp" in ModelRegistry.known_types()

    saved = ModelRegistry.snapshot()
    ModelRegistry.restore({})
    assert "temp" not in ModelRegistry.known_types()

    ModelRegistry.restore(saved)
    assert "temp" in ModelRegistry.known_types()

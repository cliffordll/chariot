"""pytest 全局配置。

集成测试(真启动 server / 连外部服务)通过 `--integration` 开关启用,
默认不跑,避免污染 `~/.chariot/` 或慢测试拖累 CI。

用法:
    uv run pytest                # 只跑单元测试(默认)
    uv run pytest --integration  # 加跑 @pytest.mark.integration
"""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--integration",
        action="store_true",
        default=False,
        help="也跑被 @pytest.mark.integration 标记的集成测试",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--integration"):
        return
    skip_marker = pytest.mark.skip(reason="需 --integration 才跑")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_marker)

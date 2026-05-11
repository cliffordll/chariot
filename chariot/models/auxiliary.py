"""AuxiliaryClient entry —— B3 wave 2 副 model 注册项的纯数据对象。

不持有 Provider 实例;只描述"哪个 provider entry + 用什么 model + 什么 sampling"。
真正的 wrapper(`AuxiliaryClient`)在 `chariot.providers.auxiliary_client` 里组装。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AuxiliaryClientEntry:
    name: str
    provider_entry: str
    model: str | None = None
    params: dict[str, Any] = field(default_factory=dict)

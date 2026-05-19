"""AuxiliaryClient entry —— B3 wave 2 副 model 注册项的纯数据对象。

不持有 Provider 实例;只描述"绑定哪个 provider + 用什么 model + 什么 sampling"。
真正的 wrapper(`AuxiliaryClient`)在 `chariot.agent.auxiliary_client` 里组装。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AuxiliaryClientEntry:
    name: str
    provider_id: str
    model: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    id: str = ""

    @classmethod
    def from_provider_id(
        cls,
        *,
        name: str,
        provider_id: str,
        model: str | None = None,
        params: dict[str, Any] | None = None,
        id: str = "",
    ) -> AuxiliaryClientEntry:
        return cls(
            name=name,
            provider_id=provider_id,
            model=model,
            params=params or {},
            id=id,
        )

"""Provider domain model.

`ProviderEntry` is the persisted-side configuration for one Provider instance
(name / type / builder options / runtime sampling params). Build a concrete
`BaseProvider` from it via `ProviderRegistry.build(type, options)`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _empty_params() -> dict[str, Any]:
    """frozen dataclass 默认值工厂;内联 lambda pyright 推断不出 dict[str, Any]。"""
    return {}


@dataclass(frozen=True)
class ProviderEntry:
    """单条 Provider 配置条目(0.6.0 起;0.3.x ~ 0.5.x 时叫 ModelEntry)。

    - `options`:build Provider 实例所需(model / api_key / base_url ...);
      `options.model` 字段(LLM model id)是 Anthropic SDK 透传字段,不在 v6
      rename 范围
    - `params`:0.3.1 加。runtime sampling 默认值(temperature / top_p / max_tokens),
      给前端发请求时填默认 body 字段用。**server 不主动注入 body**,只通过 API
      暴露给 client。
    """

    id: str
    slug: str
    name: str  # 用户面展示名
    type: str  # builder 类型 key(mock / anthropic / llama_local 等)
    options: dict[str, Any]
    params: dict[str, Any] = field(default_factory=_empty_params)

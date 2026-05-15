"""Chariot Provider 层(0.6.0+)。

`BaseProvider` 抽象基类 + 内置实现(`builtin/`)+ 注册表 + 共享 utility。

每个 Provider 是一种 LLM 后端的 Adapter:负责把 `ChatRequest` 翻译成上游
API 调用,把响应翻译回 `ChatEvent` 流。Provider 不知道工具循环、不知道
Conversation、不写 DB —— 纯输入输出。

详见 `docs/DESIGN.md` §5。
"""

from __future__ import annotations

from chariot.providers.base import BaseProvider, BaseProviderConfig
from chariot.providers.builtin.anthropic import AnthropicProvider
from chariot.providers.builtin.mock import MockProvider
from chariot.providers.builtin.openai import OpenAIProvider
from chariot.providers.registry import ProviderRegistry

# 注册内置 Provider —— 模块级单例注册(CLAUDE.md ⭐ 2:模块级单例实例化允许)。
# 调用方 import chariot.providers 后 ProviderRegistry 立即可用。
ProviderRegistry.register("mock", MockProvider)
ProviderRegistry.register("anthropic", AnthropicProvider)
ProviderRegistry.register("openai", OpenAIProvider)


__all__ = [
    "AnthropicProvider",
    "BaseProvider",
    "BaseProviderConfig",
    "MockProvider",
    "OpenAIProvider",
    "ProviderRegistry",
]

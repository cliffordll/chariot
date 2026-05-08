"""Provider 抽象基类(0.6.0+)。

替代 0.5.0 `chariot/server/model/base.py` 的 `Model` ABC。差异:

- 输入:0.5.0 是 raw JSON 字节(`body: bytes`);0.6.0 是 `ChatRequest`(typed)
- 输出:0.5.0 是 fastapi `Response`(可 stream / 可 unary);0.6.0 是统一
  `AsyncIterator[ChatEvent]`(协议无关,纯流式)
- 错误:0.5.0 通过返回值或 raise `ServiceError`;0.6.0 通过 raise `ProviderError`
  (200 前)/ yield `ChatEvent(kind="error")`(200 后),统一表达

详见 `docs/DESIGN.md` §5。

模块级零自由函数(CLAUDE.md ⭐)。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from chariot.agent.chat_event import ChatEvent
    from chariot.agent.chat_request import ChatRequest


@dataclass(frozen=True)
class BaseProviderConfig:
    """Provider 通用配置(子类可继承加专属字段)。

    `Base` 前缀表"子类可继承扩展"(对标 `BaseProvider` ABC)。子类如
    `AnthropicProviderConfig` / `OpenAIProviderConfig` 在此基础上加 api_key /
    base_url / timeout 等专属字段。

    通用字段语义:
    - `name`:entry name(用户写的,如 "claude" / "mock");chariot 内部用它路由
    - `model`:上游真实 model ID(给 LLM API,如 "claude-sonnet-4-6")
    """

    name: str
    model: str


class BaseProvider(ABC):
    """Provider 抽象基类。

    具体实现见 `chariot/providers/builtin/`(`MockProvider` /
    `AnthropicProvider` 等);0.7.0+ 加 `OpenAIProvider`,0.9.0+ 加
    `LocalLlamaProvider`。

    契约(详见 DESIGN §6.4.2):
    - 200 前的错 → raise `ProviderError(code, ...)`,由 AIAgent 转
      `ChatEvent(kind="error")` yield 给 surface
    - 200 后的错 → 直接 yield `ChatEvent(kind="error", error_type=...)` +
      return,**不抛**
    - **不产 `stream_done`**(那是 AgentLoop 跨轮收敛职责)
    - 不知道工具循环、不写 DB、不记 log —— 纯输入输出

    并发契约(0.6.5 起,长跑场景 sidecar / Gateway / ACP / MCP / Plugins 用):
    - **实例必须并发安全**:多 coroutine 并发调 `generate(req)` 各自独立产
      ChatEvent 流,不互相干扰
    - **self 上不挂 per-request mutable state**:`self.config` / `self._options`
      这种构造时确定的 immutable 字段 OK;**不要**在 generate 内写
      `self.last_req` / `self.tokens_used` 之类(并发污染)
    - **httpx client 不再由 Provider 持有**(0.6.0 旧实现持 self._client,
      0.6.5 撤);Provider 在 generate 内调 `ClientCache.get(spec)` 拿,共享
      进程级缓存,跨 Provider 实例 / 跨并发请求 keepalive 复用

    Lifecycle:
    - 实例无昂贵资源 → 不需 aclose;httpx client 由 `ClientCache` 统一管
    - per-call override(`--base-url` / `--api-key`)走"重建 Provider 实例 +
      ClientSpec 自动命中或新建 client"路径,Provider 实例本身重建零成本
    """

    config: BaseProviderConfig

    @classmethod
    @abstractmethod
    def create(cls, options: dict[str, Any]) -> Self:
        """从 ModelEntry.options 构造 Provider 实例。

        options 的 schema 由具体 Provider 定义(`AnthropicProvider` 要 api_key /
        model;`MockProvider` 不需要)。**options 不合法 raise
        `ConfigError`**(包括子类如 `InvalidProviderOptions`)。

        注:0.6.0 阶段 `options` 是从 DB `models` 表的 `options` JSON 列读出来
        的 dict;实际 Provider 实例化在 `AIAgent.bootstrap` 时统一发生。
        """

    @abstractmethod
    def generate(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        """跑一次 LLM 请求,产 ChatEvent 流(Claude 形态)。

        典型形态:
        - 纯 text:`message_start` → `content_block_start(text)` →
          `content_block_delta(text_delta)+` → `content_block_stop` →
          `message_delta(stop_reason=end_turn)` → `message_stop`
        - 含 tool_use:中间插 `content_block_start(tool_use)` +
          `content_block_delta(input_json_delta)+` + `content_block_stop`,
          `message_delta(stop_reason=tool_use)`

        注:返回 `AsyncIterator[ChatEvent]`(不是 `AsyncGenerator`),签名
        统一,允许子类用 `async def` + `yield` 或返 async iterator 对象。
        """

"""ProviderProber —— 对配置 entry 跑一次最小请求,判断该 provider 是否真能工作。

- 输入:`ProviderEntry`
- 接口:`BaseProvider.generate` 流式
- 错误体系:`ProviderError`(库化后协议无关)

工作机制
--------
1. `ProviderRegistry.build(entry.type, entry.options)` 临时构造 Provider
   - 失败(api_key 缺 / type 未注册等)→ `ProbeResult(ok=False, code='config_error')`
2. 跑最小 ChatRequest(`ping`,`max_tokens=1`),streaming
3. 等首次 `message_stop` event(success)/ `error` event / 抛 ProviderError
4. 临时 Provider 实例不显式 close;返回后 GC

费用警告
--------
真打上游 1 次,**消耗 ~1 token**(取决于上游计费规则);UI 上要明确告知用户。
MockProvider 走纯本地路径,probe 不发 HTTP、零费用。

封装
----
`ProviderProber` 是无状态工具类,只暴露 `probe(entry)` classmethod。
模块级零自由函数 / 零可变变量(常量是 ClassVar)。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import ClassVar

from chariot.agent.chat_request import ChatRequest, Message
from chariot.agent.config import ProviderEntry
from chariot.agent.exceptions import ConfigError, ProviderError
from chariot.providers.base import BaseProvider
from chariot.providers.registry import ProviderRegistry


@dataclass(frozen=True, slots=True)
class ProbeError:
    """探针失败时的错误结构。

    `code` 透传:
    - `ProviderError.code`:`upstream_auth_failed` / `upstream_unreachable` /
      `upstream_server_error` / `rate_limited` / `upstream_stream_error`
    - chariot 内置:`config_error`(build 阶段失败)/ `probe_internal_error`
      (兜底)/ `incomplete_stream`(没收到 message_stop / error 就结束)
    """

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """探针结果。`ok=True` 时 error=None;`ok=False` 时 error 必给。"""

    ok: bool
    latency_ms: int
    error: ProbeError | None = None


class ProviderProber:
    """探针工具类 —— 给一条 ProviderEntry,临时 build + 发最小请求 + 报结果。"""

    _PROBE_PROMPT: ClassVar[str] = "ping"
    _PROBE_MAX_TOKENS: ClassVar[int] = 1

    @classmethod
    async def probe(cls, entry: ProviderEntry) -> ProbeResult:
        """对 entry 执行一次探针。永不 raise —— 任何失败都包成 ProbeResult。"""
        t0 = time.monotonic()
        try:
            provider = ProviderRegistry.build(entry.type, entry.options)
        except ConfigError as e:
            return ProbeResult(
                ok=False,
                latency_ms=0,
                error=ProbeError(code="config_error", message=str(e)),
            )

        req = ChatRequest(
            provider_name=entry.name,
            messages=[Message(role="user", content=cls._PROBE_PROMPT)],
            max_tokens=cls._PROBE_MAX_TOKENS,
        )
        return await cls._consume_probe_stream(provider, req, t0)

    @classmethod
    async def _consume_probe_stream(
        cls,
        provider: BaseProvider,
        req: ChatRequest,
        t0: float,
    ) -> ProbeResult:
        """消费 provider.generate 流,返第一个有意义信号(success / error)。"""
        try:
            async for event in provider.generate(req):
                if event.kind == "error":
                    return ProbeResult(
                        ok=False,
                        latency_ms=cls._elapsed_ms(t0),
                        error=ProbeError(
                            code=event.error_type or "unknown",
                            message=event.error_message or "",
                        ),
                    )
                if event.kind == "message_stop":
                    return ProbeResult(ok=True, latency_ms=cls._elapsed_ms(t0))
        except ProviderError as e:
            return ProbeResult(
                ok=False,
                latency_ms=cls._elapsed_ms(t0),
                error=ProbeError(code=e.code, message=e.message),
            )
        except Exception as e:  # pragma: no cover — 兜底
            return ProbeResult(
                ok=False,
                latency_ms=cls._elapsed_ms(t0),
                error=ProbeError(code="probe_internal_error", message=str(e)),
            )

        # 流自然结束但没 message_stop / error
        return ProbeResult(
            ok=False,
            latency_ms=cls._elapsed_ms(t0),
            error=ProbeError(
                code="incomplete_stream",
                message="provider stream ended without message_stop or error",
            ),
        )

    @staticmethod
    def _elapsed_ms(t0: float) -> int:
        return int((time.monotonic() - t0) * 1000)

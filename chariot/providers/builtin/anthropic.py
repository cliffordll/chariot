"""AnthropicProvider —— 走 Anthropic Messages API,产 Claude 形态 ChatEvent。

替代 0.5.0 `chariot/server/model/anthropic.py` 的 `AnthropicModel`。差异:

- **不再透传字节**:body 从 `ChatRequest` 用 `dataclasses.asdict()` 拼装
  (跟 Claude API 1:1,所以几乎是直接 dump);响应 SSE 帧解析后 yield 出
  `ChatEvent`(typed),不返 `StreamingResponse`
- **错误处理改 `ProviderError`**(去 fastapi 依赖):
  - 200 前的错 → `raise ProviderError(code="upstream_*", ...)`
  - 200 后的错 → yield `ChatEvent(kind="error", error_type="upstream_stream_error")`
    + return(沿用 0.1.0"200 已发后断 TCP 不伪造事件"契约,只是表达从"断 TCP"
    升级为"显式 error event")

错误码映射(沿用 0.5.0):
- 401 / 403 → `upstream_auth_failed`
- 5xx → `upstream_server_error`
- httpx 网络异常(超时 / 连接拒绝)→ `upstream_unreachable`
- 429 → `rate_limited`
- 200 后 IO 错 → `upstream_stream_error`(yield event,不抛)

封装:client / model / api_key 等都在实例字段;模块级零自由函数(CLAUDE.md ⭐)。
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import AsyncIterator
from typing import Any, ClassVar, Self, cast

import httpx

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest
from chariot.agent.exceptions import ConfigError, ProviderError
from chariot.providers._sse import SseParser
from chariot.providers.base import BaseProvider, BaseProviderConfig


class AnthropicProvider(BaseProvider):
    """Anthropic Messages API 适配器。

    构造:走 `from_options(options)`(由 `ProviderRegistry.build` 调);
    手工构造 `__init__` 也允许,主要给测试 inject 自定义 httpx client 用
    (传 `client=httpx.AsyncClient(transport=MockTransport(...))`)。
    """

    _DEFAULT_BASE_URL: ClassVar[str] = "https://api.anthropic.com"
    _DEFAULT_API_KEY_ENV: ClassVar[str] = "ANTHROPIC_API_KEY"
    _ANTHROPIC_VERSION: ClassVar[str] = "2023-06-01"

    # 类级超时常量,测试可 monkeypatch
    _CONNECT_TIMEOUT_SEC: ClassVar[float] = 10.0
    _READ_TIMEOUT_SEC: ClassVar[float] = 300.0
    _WRITE_TIMEOUT_SEC: ClassVar[float] = 30.0
    _POOL_TIMEOUT_SEC: ClassVar[float] = 10.0

    # 不透给上游的 chariot 扩展字段(从 ChatRequest dict 里剔除后再发)
    _CHARIOT_EXTENSION_FIELDS: ClassVar[frozenset[str]] = frozenset({"conversation_id", "agent_id"})

    def __init__(
        self,
        *,
        config: BaseProviderConfig,
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """构造 AnthropicProvider。

        - `client=None` → 自建 `httpx.AsyncClient`(生产路径)
        - `client=<注入>` → 测试用,可塞 `MockTransport`(直接复用 headers /
          base_url 为 None,只用注入 client 的 transport)
        """
        self.config = config
        self._api_key = api_key
        self._base_url = base_url
        if client is not None:
            self._client = client
            return
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "x-api-key": api_key,
                "anthropic-version": self._ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            timeout=httpx.Timeout(
                connect=self._CONNECT_TIMEOUT_SEC,
                read=self._READ_TIMEOUT_SEC,
                write=self._WRITE_TIMEOUT_SEC,
                pool=self._POOL_TIMEOUT_SEC,
            ),
        )

    # ---- 构造契约 ----

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> Self:
        """从 `ModelEntry.options` 构造;关键字段缺 / 非法 → `ConfigError`。

        options 字段:
        - `model`(必填):上游真实 model ID(如 `claude-sonnet-4-6`)
        - `api_key`(可选):内联密钥;不填则从 env 读
        - `api_key_env`(可选,默认 `ANTHROPIC_API_KEY`):环境变量名
        - `base_url`(可选,默认 `https://api.anthropic.com`)
        """
        model = options.get("model")
        if not isinstance(model, str) or not model:
            raise ConfigError("anthropic provider 配置缺少 'model' 或类型不对")

        api_key = cls._resolve_api_key(options)

        base_url = options.get("base_url", cls._DEFAULT_BASE_URL)
        if not isinstance(base_url, str) or not base_url:
            raise ConfigError("'base_url' 必须是非空字符串")

        config = BaseProviderConfig(name="anthropic", model=model)
        return cls(config=config, api_key=api_key, base_url=base_url)

    @classmethod
    def _resolve_api_key(cls, options: dict[str, Any]) -> str:
        """按优先级 (inline api_key) → (api_key_env 指向的 env) 解析 key。"""
        inline = options.get("api_key")
        if inline is not None:
            if not isinstance(inline, str) or not inline:
                raise ConfigError("'api_key' 必须是非空字符串(留空请整条删掉)")
            return inline

        env_name = options.get("api_key_env", cls._DEFAULT_API_KEY_ENV)
        if not isinstance(env_name, str) or not env_name:
            raise ConfigError("'api_key_env' 必须是非空字符串")
        env_value = os.environ.get(env_name)
        if not env_value:
            raise ConfigError(f"未拿到 api_key:配置里没填 'api_key',且环境变量 {env_name} 也未设置")
        return env_value

    # ---- BaseProvider 接口 ----

    async def generate(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        """跑一次 Anthropic Messages API 请求,产 Claude 形态 ChatEvent 流。

        body 从 ChatRequest 拼,**强制 stream=True**(chariot 内核固定流式;
        ChatRequest 自身没 stream 字段,这里在拼 body 时塞)。
        """
        body = self._build_body(req)
        try:
            send_req = self._client.build_request("POST", "/v1/messages", json=body)
            upstream = await self._client.send(send_req, stream=True)
        except httpx.TimeoutException as e:
            raise ProviderError("upstream_unreachable", f"上游超时: {e}") from e
        except httpx.HTTPError as e:
            raise ProviderError("upstream_unreachable", f"上游不可达: {e}") from e

        # 200 前的错:读 body + 关连接 + 抛
        if upstream.status_code != 200:
            try:
                await upstream.aread()
            finally:
                await upstream.aclose()
            self._raise_for_non_200(upstream)

        # 200 已发,切到流式 yield ChatEvent。任何后续异常都转 error event +
        # return,不抛(0.1.0 契约)。
        try:
            async for event_name, data in SseParser.iter_frames(upstream.aiter_bytes()):
                event = self._frame_to_event(event_name, data)
                if event is not None:
                    yield event
        except (httpx.HTTPError, OSError, RuntimeError) as e:
            yield ChatEvent.error_event(
                error_type="upstream_stream_error",
                error_message=f"流式中途中断: {e}",
            )
            return
        finally:
            await upstream.aclose()

    # ---- 内部:body 拼装 ----

    @classmethod
    def _build_body(cls, req: ChatRequest) -> dict[str, Any]:
        """`ChatRequest` → Anthropic API request body dict。

        步骤:
        1. `dataclasses.asdict(req)` 拿全字段
        2. 剔除 chariot 扩展字段(`conversation_id` / `agent_id`)
        3. 剔除 `None` 值字段(Anthropic API 不接受 null,且 max_tokens 等
          有默认 4096 不能漏)
        4. 强制 `stream=True`(chariot 内核固定流式)
        """
        full = dataclasses.asdict(req)
        body: dict[str, Any] = {
            k: v
            for k, v in full.items()
            if k not in cls._CHARIOT_EXTENSION_FIELDS and v is not None
        }
        body["stream"] = True
        return body

    # ---- 内部:HTTP 错误码映射 ----

    @staticmethod
    def _raise_for_non_200(upstream: httpx.Response) -> None:
        """200 前的错 → 抛 ProviderError(对应 code)。

        映射规则:
        - 401 / 403 → upstream_auth_failed
        - 429 → rate_limited
        - 5xx → upstream_server_error
        - 其它 4xx → upstream_server_error(也归 502 范畴,详细 message 带原 body 截断)
        """
        sc = upstream.status_code
        body_preview = upstream.content[:200].decode("utf-8", errors="replace")
        if sc in (401, 403):
            raise ProviderError(
                "upstream_auth_failed",
                f"上游认证失败({sc}):检查 api_key / api_key_env",
            )
        if sc == 429:
            raise ProviderError("rate_limited", f"上游限流(429): {body_preview}", status=429)
        if 500 <= sc < 600:
            raise ProviderError("upstream_server_error", f"上游 {sc}: {body_preview}")
        raise ProviderError("upstream_server_error", f"上游错误({sc}): {body_preview}")

    # ---- 内部:SSE 帧 → ChatEvent ----

    @staticmethod
    def _frame_to_event(event_name: str | None, data: dict[str, Any]) -> ChatEvent | None:
        """Anthropic SSE 帧 → ChatEvent(几乎 1:1)。

        优先用 `data["type"]`(更稳;event 行偶有缺失);unknown 类型 → None
        (跳过,日志层捕获)。
        """
        kind = data.get("type") or event_name
        if not isinstance(kind, str):
            return None

        match kind:
            case "message_start":
                msg = AnthropicProvider._dict_or_empty(data.get("message"))
                usage = AnthropicProvider._dict_or_none(msg.get("usage"))
                return ChatEvent(kind="message_start", message=msg, usage=usage)
            case "content_block_start":
                return ChatEvent(
                    kind="content_block_start",
                    index=AnthropicProvider._int_or_none(data.get("index")),
                    content_block=AnthropicProvider._dict_or_none(data.get("content_block")),
                )
            case "content_block_delta":
                return ChatEvent(
                    kind="content_block_delta",
                    index=AnthropicProvider._int_or_none(data.get("index")),
                    delta=AnthropicProvider._dict_or_none(data.get("delta")),
                )
            case "content_block_stop":
                return ChatEvent(
                    kind="content_block_stop",
                    index=AnthropicProvider._int_or_none(data.get("index")),
                )
            case "message_delta":
                return ChatEvent(
                    kind="message_delta",
                    delta=AnthropicProvider._dict_or_none(data.get("delta")),
                    usage=AnthropicProvider._dict_or_none(data.get("usage")),
                )
            case "message_stop":
                return ChatEvent(kind="message_stop")
            case "ping":
                return ChatEvent(kind="ping")
            case "error":
                err = AnthropicProvider._dict_or_empty(data.get("error"))
                err_type_raw = err.get("type", "unknown")
                err_msg_raw = err.get("message", "")
                err_type = err_type_raw if isinstance(err_type_raw, str) else "unknown"
                err_msg = err_msg_raw if isinstance(err_msg_raw, str) else ""
                return ChatEvent.error_event(error_type=err_type, error_message=err_msg)
            case _:
                # 未知 event type:跳过(Anthropic 后续可能加新 event,新加 kind 时
                # 再补 case;旧版本不识别就忽略,不破坏既有流)
                return None

    # ---- 类型守卫(把 Any 收成具体类型,让 pyright 推得出) ----

    @staticmethod
    def _dict_or_empty(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return cast(dict[str, Any], value)
        return {}

    @staticmethod
    def _dict_or_none(value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            return cast(dict[str, Any], value)
        return None

    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        return value if isinstance(value, int) else None

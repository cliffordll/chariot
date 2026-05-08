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

0.6.5 起架构调整(Hermes 哲学):
- **不再持有 httpx client**:`self._client` 撤;实例只持 `_options` + `_spec`
- httpx client 由 `ClientCache.get(spec)` 进程级共享(LRU + 并发安全)
- per-call override(`--base-url`/`--api-key`)走"重建 Provider 实例 +
  ClientSpec 自动命中或新建 client"路径,不重建已存在 (base_url, api_key)
  对应的 client(连接 keepalive 保住)

封装:`_options` / `_spec` 都是构造时 immutable;模块级零自由函数(CLAUDE.md ⭐)。
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
from chariot.providers.clients import ClientCache, ClientSpec


class AnthropicProvider(BaseProvider):
    """Anthropic Messages API 适配器。

    0.6.5 起构造:走 `from_options(options)`(由 `ProviderRegistry.build` 调);
    实例持 `self._options`(merged effective options)+ `self._spec`(ClientSpec,
    构造时算一次,后续 generate 复用)。**不持 httpx client** —— client 由
    `ClientCache` 进程级缓存,Provider 重建零成本。

    并发安全(详 BaseProvider 类 docstring):实例所有字段 immutable;
    `generate(req)` 内部从 `ClientCache.get(self._spec)` 拿 client,httpx 自身
    保证 client 跨 coroutine 共用安全。
    """

    _DEFAULT_BASE_URL: ClassVar[str] = "https://api.anthropic.com"
    _DEFAULT_API_KEY_ENV: ClassVar[str] = "ANTHROPIC_API_KEY"
    _BASE_URL_ENV: ClassVar[str] = "ANTHROPIC_BASE_URL"  # Anthropic SDK 标准 env
    _ANTHROPIC_VERSION: ClassVar[str] = "2023-06-01"

    # 类级超时常量(默认值;options 不开放覆盖,如要调整改类常量)
    _CONNECT_TIMEOUT_SEC: ClassVar[float] = 10.0
    _READ_TIMEOUT_SEC: ClassVar[float] = 300.0
    _WRITE_TIMEOUT_SEC: ClassVar[float] = 30.0
    _POOL_TIMEOUT_SEC: ClassVar[float] = 10.0

    # httpx 连接池上限(0.6.5 起;options 可覆盖,Gateway 高并发场景调高)
    _DEFAULT_MAX_CONNECTIONS: ClassVar[int] = 20
    _DEFAULT_MAX_KEEPALIVE: ClassVar[int] = 10

    # 不透给上游的 chariot 扩展字段(从 ChatRequest dict 里剔除后再发);
    # `provider_name` 是 chariot 的路由 key,wire body 里不带这个字段(`model`
    # 用 `self.config.model` 显式写入)
    _CHARIOT_EXTENSION_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"provider_name", "convo_id", "agent_id"}
    )

    def __init__(
        self,
        *,
        config: BaseProviderConfig,
        options: dict[str, Any],
    ) -> None:
        """构造 AnthropicProvider(0.6.5 起接口)。

        实例持 effective options + 构造时算一次的 ClientSpec(给 generate 复用)。
        校验失败(api_key / base_url 不合法)在 `_build_spec` 阶段抛 ConfigError。

        测试场景:`from_options(...)` 构造 + monkeypatch `ClientCache.get` 注入
        mock httpx client(详见 tests/providers/builtin/test_anthropic.py 的
        `_make_provider` helper)。
        """
        self.config = config
        self._options = dict(options)
        self._spec = self._build_spec()

    # ---- 构造契约 ----

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> Self:
        """从 `ProviderEntry.options` 构造;关键字段缺 / 非法 → `ConfigError`。

        options 字段(优先级 = inline → env → 默认;CLI flag 通过 inline 注入):
        - `model`(必填):上游真实 model ID(如 `claude-sonnet-4-6`);仅 inline,无 env
        - `api_key`(可选 inline):内联密钥
        - `api_key_env`(可选,默认 `ANTHROPIC_API_KEY`):环境变量名;inline 缺时读
        - `base_url`(可选 inline):内联;缺时读 `ANTHROPIC_BASE_URL` env;
          再缺用默认 `https://api.anthropic.com`(Anthropic SDK 标准约定)
        - `max_connections`(可选,默认 20):httpx 连接池上限
        - `max_keepalive`(可选,默认 10):httpx keepalive 连接上限
        """
        model = options.get("model")
        if not isinstance(model, str) or not model:
            raise ConfigError("anthropic provider 配置缺少 'model' 或类型不对")

        config = BaseProviderConfig(name="anthropic", model=model)
        # api_key / base_url 校验在 __init__ → _build_spec 里发生
        return cls(config=config, options=options)

    def _build_spec(self) -> ClientSpec:
        """从 `self._options` 算 ClientSpec;校验失败抛 `ConfigError`。

        构造时调一次;后续 generate 用 `self._spec` 命中 ClientCache。同 spec 的
        多个 Provider 实例(per-call override 重建)共用同一个 cached client。
        """
        api_key = self._resolve_api_key(self._options)
        base_url = self._resolve_base_url(self._options)
        max_conn_raw = self._options.get("max_connections", self._DEFAULT_MAX_CONNECTIONS)
        max_keep_raw = self._options.get("max_keepalive", self._DEFAULT_MAX_KEEPALIVE)
        return ClientSpec(
            provider_type="anthropic",
            base_url=base_url,
            api_key=api_key,
            headers=(
                ("x-api-key", api_key),
                ("anthropic-version", self._ANTHROPIC_VERSION),
                ("content-type", "application/json"),
            ),
            max_connections=int(max_conn_raw),
            max_keepalive=int(max_keep_raw),
            connect_timeout_sec=self._CONNECT_TIMEOUT_SEC,
            read_timeout_sec=self._READ_TIMEOUT_SEC,
            write_timeout_sec=self._WRITE_TIMEOUT_SEC,
            pool_timeout_sec=self._POOL_TIMEOUT_SEC,
        )

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

    @classmethod
    def _resolve_base_url(cls, options: dict[str, Any]) -> str:
        """按优先级 (inline base_url) → (`ANTHROPIC_BASE_URL` env) → 默认 解析。

        env 名固定 `ANTHROPIC_BASE_URL`(对齐 Anthropic Python SDK 约定),不
        提供 `base_url_env` 配置字段(否则跟 SDK 约定割裂,迁移摩擦增大)。
        """
        inline = options.get("base_url")
        if inline is not None:
            if not isinstance(inline, str) or not inline:
                raise ConfigError("'base_url' 必须是非空字符串(留空请整条删掉)")
            return inline

        env_value = os.environ.get(cls._BASE_URL_ENV)
        if env_value:
            return env_value

        return cls._DEFAULT_BASE_URL

    # ---- BaseProvider 接口 ----

    async def generate(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        """跑一次 Anthropic Messages API 请求,产 Claude 形态 ChatEvent 流。

        body 从 ChatRequest 拼,**强制 stream=True**(chariot 内核固定流式;
        ChatRequest 自身没 stream 字段,这里在拼 body 时塞)。

        client 从 `ClientCache.get(self._spec)` 拿:同 (base_url, api_key, ...)
        命中已有 client,连接 keepalive 复用;未命中构造 + 缓存。
        """
        body = self._build_body(req)
        client = await ClientCache.get(self._spec)
        try:
            send_req = client.build_request("POST", "/v1/messages", json=body)
            upstream = await client.send(send_req, stream=True)
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

    def _build_body(self, req: ChatRequest) -> dict[str, Any]:
        """`ChatRequest` → Anthropic API request body dict。

        步骤:
        1. `dataclasses.asdict(req)` 拿全字段
        2. 剔除 chariot 扩展字段(`provider_name` / `convo_id` / `agent_id`);
          `provider_name` 是 chariot 路由 key,Anthropic API 不识别
        3. 剔除 `None` 值字段(Anthropic API 不接受 null,且 max_tokens 等
          有默认 4096 不能漏)
        4. **写 `body["model"] = req.model or self.config.model`**(0.6.5+):
          per-call `req.model` 优先(CLI `--model` flag 走这条);缺省回退到
          `self.config.model`(实例化时从 entry.options.model 落)。注意 step 3
          已把 `model=None` 滤掉,这步是单独覆写,确保非 None 时 wire 字段写对
        5. 强制 `stream=True`(chariot 内核固定流式)
        """
        full = dataclasses.asdict(req)
        body: dict[str, Any] = {
            k: v
            for k, v in full.items()
            if k not in self._CHARIOT_EXTENSION_FIELDS and v is not None
        }
        body["model"] = req.model or self.config.model
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

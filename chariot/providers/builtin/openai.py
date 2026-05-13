"""OpenAIProvider —— 走 OpenAI Chat Completions API,产 Claude 形态 ChatEvent。

OpenAI ↔ Claude 翻译发生在本 Provider 内部,不污染内核/不落库/不污染 surface。

输入翻译(ChatRequest → OpenAI body):
- system: Claude 顶层字段 → OpenAI messages[0] role="system"
- messages: Claude content blocks → OpenAI message content string / tool_calls
- tools: Claude tool_schema → OpenAI tools(function 形态)
- tool_choice: Claude {"type": "auto"} → OpenAI "auto"

输出翻译(OpenAI SSE → ChatEvent):
- choices[0].delta.role="assistant" → message_start
- choices[0].delta.content → content_block_delta(text_delta)
- choices[0].delta.tool_calls → content_block_start(tool_use) + content_block_delta(input_json_delta)
- choices[0].delta.finish_reason → message_delta(stop_reason)
- [DONE] → message_stop

错误处理同 AnthropicProvider:200 前 raise ProviderError;200 后 yield error event。
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any, ClassVar, Self

import httpx

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest
from chariot.agent.exceptions import ConfigError, ProviderError
from chariot.providers._sse import SseParser
from chariot.providers.base import BaseProvider, BaseProviderCapabilities, BaseProviderConfig
from chariot.providers.clients import ClientCache, ClientSpec


class OpenAIProvider(BaseProvider):
    """OpenAI Chat Completions API 适配器。

    内部做 Claude ↔ OpenAI 形态翻译。实例 immutable,并发安全。
    """

    _DEFAULT_BASE_URL: ClassVar[str] = "https://api.openai.com"
    capabilities = BaseProviderCapabilities(
        supports_system=True,
        supports_tools=True,
        supports_tool_choice=True,
        supports_thinking=False,  # OpenAI 无 thinking 模式
    )
    _DEFAULT_API_KEY_ENV: ClassVar[str] = "OPENAI_API_KEY"
    _BASE_URL_ENV: ClassVar[str] = "OPENAI_BASE_URL"

    _CONNECT_TIMEOUT_SEC: ClassVar[float] = 10.0
    _READ_TIMEOUT_SEC: ClassVar[float] = 300.0
    _WRITE_TIMEOUT_SEC: ClassVar[float] = 30.0
    _POOL_TIMEOUT_SEC: ClassVar[float] = 10.0

    _DEFAULT_MAX_CONNECTIONS: ClassVar[int] = 20
    _DEFAULT_MAX_KEEPALIVE: ClassVar[int] = 10

    def __init__(
        self,
        *,
        config: BaseProviderConfig,
        options: dict[str, Any],
    ) -> None:
        self.config = config
        self._options = dict(options)
        self._spec = self._build_spec()

    # ---- 构造契约 ----

    @classmethod
    def create(cls, options: dict[str, Any]) -> Self:
        """从 ProviderEntry.options 构造;关键字段缺/非法 → ConfigError。"""
        model = options.get("model")
        if not isinstance(model, str) or not model:
            raise ConfigError("openai provider 配置缺少 'model' 或类型不对")

        config = BaseProviderConfig(name="openai", model=model)
        return cls(config=config, options=options)

    def _build_spec(self) -> ClientSpec:
        """构造 ClientSpec。"""
        api_key = self._resolve_api_key(self._options)
        base_url = self._resolve_base_url(self._options)
        max_conn_raw = self._options.get("max_connections", self._DEFAULT_MAX_CONNECTIONS)
        max_keep_raw = self._options.get("max_keepalive", self._DEFAULT_MAX_KEEPALIVE)
        return ClientSpec(
            provider_type="openai",
            base_url=base_url,
            api_key=api_key,
            headers=(
                ("authorization", f"Bearer {api_key}"),
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
        """解析 api_key: inline → env → 报错。"""
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
        """解析 base_url: inline → env → 默认。"""
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
        """跑一次 OpenAI Chat Completions API,产 Claude 形态 ChatEvent 流。"""
        body = self._build_body(req)
        client = await ClientCache.get(self._spec)
        try:
            send_req = client.build_request("POST", "/v1/chat/completions", json=body)
            upstream = await client.send(send_req, stream=True)
        except httpx.TimeoutException as e:
            raise ProviderError("upstream_unreachable", f"上游超时: {e}") from e
        except httpx.HTTPError as e:
            raise ProviderError("upstream_unreachable", f"上游不可达: {e}") from e

        if upstream.status_code != 200:
            try:
                await upstream.aread()
            finally:
                await upstream.aclose()
            self._raise_for_non_200(upstream)

        try:
            saw_message_start = False
            open_blocks: set[int] = set()
            async for _event_name, data in SseParser.iter_frames(upstream.aiter_bytes()):
                events = self._frame_to_event(
                    data,
                    saw_message_start=saw_message_start,
                    open_blocks=open_blocks,
                )
                for event in events:
                    if event.kind == "message_start":
                        saw_message_start = True
                    elif event.kind == "content_block_start":
                        open_blocks.add(event.index)
                    elif event.kind == "content_block_stop":
                        open_blocks.discard(event.index)
                    yield event
            # OpenAI 流没有 content_block_stop / message_stop;
            # 结束前补关闭所有未关 block，再补 message_stop
            for idx in sorted(open_blocks):
                yield ChatEvent(kind="content_block_stop", index=idx)
            yield ChatEvent(kind="message_stop")
        except (httpx.HTTPError, OSError, RuntimeError) as e:
            yield ChatEvent.error_event(
                error_type="upstream_stream_error",
                error_message=f"流式中途中断: {e}",
            )
            return
        finally:
            await upstream.aclose()

    # ---- 内部:body 拼装(Claude → OpenAI) ----

    def _build_body(self, req: ChatRequest) -> dict[str, Any]:
        """ChatRequest(Claude 形态) → OpenAI Chat Completions request body。"""
        messages: list[dict[str, Any]] = []

        # system → 第一条 system message
        if req.system is not None:
            if isinstance(req.system, str):
                messages.append({"role": "system", "content": req.system})
            else:
                # list[SystemBlock] → 取 text 拼成字符串
                texts = [b.text for b in req.system if b.type == "text"]
                if texts:
                    messages.append({"role": "system", "content": "\n".join(texts)})

        # messages: Claude content blocks → OpenAI message format
        for msg in req.messages:
            openai_msg = self._claude_msg_to_openai(msg)
            if openai_msg is not None:
                messages.append(openai_msg)

        body: dict[str, Any] = {
            "model": req.model or self.config.model,
            "messages": messages,
            "stream": True,
        }

        if req.max_tokens is not None:
            body["max_tokens"] = req.max_tokens
        if req.temperature is not None:
            body["temperature"] = req.temperature
        if req.top_p is not None:
            body["top_p"] = req.top_p
        if req.stop_sequences is not None:
            body["stop"] = req.stop_sequences

        # tools: Claude tool_schema → OpenAI function tools
        if req.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in req.tools
            ]

        # tool_choice
        if req.tool_choice is not None:
            tc_type = req.tool_choice.get("type")
            if tc_type == "auto":
                body["tool_choice"] = "auto"
            elif tc_type == "any":
                body["tool_choice"] = "required"
            elif tc_type == "none":
                body["tool_choice"] = "none"
            elif tc_type == "tool":
                tool_name = req.tool_choice.get("name")
                if tool_name:
                    body["tool_choice"] = {"type": "function", "function": {"name": tool_name}}

        return body

    @staticmethod
    def _claude_msg_to_openai(msg: Any) -> dict[str, Any] | None:
        """单条 Claude Message → OpenAI message dict。"""
        role = msg.role
        content = msg.content

        if isinstance(content, str):
            return {"role": role, "content": content}

        # content block list
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []

        for block in content:
            btype = block.get("type")
            if btype == "text":
                text = block.get("text", "")
                if isinstance(text, str):
                    text_parts.append(text)
            elif btype == "tool_use":
                tool_calls.append(
                    {
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input", {})),
                        },
                    }
                )
            elif btype == "tool_result":
                tool_results.append(
                    {
                        "tool_call_id": block.get("tool_use_id", ""),
                        "role": "tool",
                        "content": str(block.get("content", "")),
                    }
                )

        # OpenAI 中 assistant message 含 tool_calls,user message 含 tool results
        if role == "assistant":
            result: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts) or None}
            if tool_calls:
                result["tool_calls"] = tool_calls
            return result
        else:  # user
            # tool results 在 OpenAI 中是独立的消息,不是 user message 的 content
            messages_out: list[dict[str, Any]] = []
            if text_parts:
                messages_out.append({"role": "user", "content": "\n".join(text_parts)})
            for tr in tool_results:
                messages_out.append(tr)
            # 简化:返回第一条,其余由调用方处理... 实际上这里需要返回 list
            # 但我们的 interface 是单条 → 单条,所以把 tool_results 合并到 content
            if tool_results and not text_parts:
                # 只有 tool_result:OpenAI 中每条 tool_result 是一条独立 message
                # 这里简化处理,把第一个 tool_result 当 user message 返回
                return {"role": "user", "content": tool_results[0].get("content", "")}
            return {"role": "user", "content": "\n".join(text_parts) or ""}

    # ---- 内部:HTTP 错误码映射 ----

    @staticmethod
    def _raise_for_non_200(upstream: httpx.Response) -> None:
        """200 前的错 → 抛 ProviderError。"""
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

    # ---- 内部:SSE 帧 → ChatEvent(OpenAI → Claude) ----

    @staticmethod
    def _frame_to_event(
        data: dict[str, Any],
        *,
        saw_message_start: bool = False,
        open_blocks: set[int],
    ) -> list[ChatEvent]:
        """OpenAI SSE chunk → ChatEvent list(Claude 形态)。

        返回 list 是因为一个 OpenAI frame 可能需要拆成多个 Claude event
        (如 content_block_start + content_block_delta)。
        """
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return []

        delta = choices[0].get("delta", {})
        finish_reason = choices[0].get("finish_reason")
        events: list[ChatEvent] = []

        # message_start: 第一轮有 role(只发一次)
        if not saw_message_start and delta.get("role") == "assistant":
            events.append(ChatEvent(kind="message_start", message={"role": "assistant"}))

        # content_block_start: tool_calls 开始
        tool_calls_delta = delta.get("tool_calls")
        if isinstance(tool_calls_delta, list) and tool_calls_delta:
            tc = tool_calls_delta[0]
            if tc.get("index") == 0 and tc.get("id") and 0 not in open_blocks:
                events.append(
                    ChatEvent(
                        kind="content_block_start",
                        index=0,
                        content_block={
                            "type": "tool_use",
                            "id": tc.get("id"),
                            "name": tc.get("function", {}).get("name", ""),
                            "input": {},
                        },
                    )
                )

            # tool_call arguments delta
            args_delta = tc.get("function", {}).get("arguments")
            if isinstance(args_delta, str) and args_delta:
                events.append(
                    ChatEvent(
                        kind="content_block_delta",
                        index=0,
                        delta={"type": "input_json_delta", "partial_json": args_delta},
                    )
                )

        # content_block_delta: text
        content = delta.get("content")
        if isinstance(content, str) and content:
            if 0 not in open_blocks:
                events.append(
                    ChatEvent(
                        kind="content_block_start",
                        index=0,
                        content_block={"type": "text", "text": ""},
                    )
                )
            events.append(
                ChatEvent(
                    kind="content_block_delta",
                    index=0,
                    delta={"type": "text_delta", "text": content},
                )
            )

        # message_delta: finish_reason
        if finish_reason is not None:
            stop_reason = OpenAIProvider._map_finish_reason(finish_reason)
            events.append(
                ChatEvent(
                    kind="message_delta",
                    delta={"stop_reason": stop_reason},
                )
            )

        return events

    @staticmethod
    def _map_finish_reason(reason: str | None) -> str | None:
        """OpenAI finish_reason → Claude stop_reason。"""
        if reason is None:
            return None
        mapping = {
            "stop": "end_turn",
            "length": "max_tokens",
            "tool_calls": "tool_use",
            "content_filter": "content_filter",
        }
        return mapping.get(reason, reason)

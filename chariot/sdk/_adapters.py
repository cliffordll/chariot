"""SDK 侧协议 adapter —— chat.py / streams.py 共用的三协议知识。

和 server 侧 `chariot.server.model.mock.ProtocolAdapter` 对称,但关注的是
**客户端**的事:怎么拼请求 body、怎么从响应 / SSE 事件里抽文本 + usage。

每个协议一个 adapter 实例。`adapter_for(fmt)` 查表拿对应实现;chat.py 和
streams.py 都通过它发散调用,自己不再做 `if fmt is MESSAGES / ...` 分支。

加新协议
--------
写一个 `FooAdapter(ProtocolAdapter)`,挂进 `_ADAPTERS` dispatch 表。
其它不动。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, cast

from chariot.shared.protocols import Protocol


@dataclass
class UsageState:
    """流式解码过程中累加的 usage(input_tokens / output_tokens)。

    `ChatStream` 挂一个实例,SSE 事件到来时各 adapter 按自家规则 **原地更新** 字段。
    非流路径不用,直接 `adapter.extract_usage_once()` 返 dict。
    """

    input_tokens: int = 0
    output_tokens: int = 0

    def snapshot(self) -> dict[str, int]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


class ProtocolAdapter(ABC):
    """每个 API 协议一个 adapter;无状态,每 Protocol 只实例化一次。"""

    # ---- 请求侧(非流 + 流共用) ----

    @abstractmethod
    def build_request_body(
        self,
        text: str,
        *,
        model: str,
        max_tokens: int,
        stream: bool,
    ) -> dict[str, Any]:
        """把单轮 user 输入 + 参数拼成协议合法的请求 body(顶层 dict)。"""

    # ---- 非流响应侧 ----

    @abstractmethod
    def extract_text_once(self, data: dict[str, Any]) -> str:
        """从非流响应 dict 抽合并后的 assistant 文本。"""

    @abstractmethod
    def extract_usage_once(self, data: dict[str, Any]) -> dict[str, int]:
        """从非流响应 dict 抽 usage;返回统一的 `{input_tokens, output_tokens}`。"""

    # ---- 流式侧 ----

    @abstractmethod
    def extract_text_delta(self, event_name: str | None, data: dict[str, Any]) -> str:
        """从单条 SSE 事件抽文本增量;非文本事件返空串。"""

    @abstractmethod
    def update_usage(
        self,
        event_name: str | None,
        data: dict[str, Any],
        state: UsageState,
    ) -> None:
        """按单条 SSE 事件更新 UsageState(原地写)。没 usage 信息的事件直接 no-op。"""


# ================================================================
#  Anthropic Messages /v1/messages
# ================================================================


class MessagesAdapter(ProtocolAdapter):
    def build_request_body(
        self, text: str, *, model: str, max_tokens: int, stream: bool
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": text}],
        }
        if stream:
            body["stream"] = True
        return body

    def extract_text_once(self, data: dict[str, Any]) -> str:
        blocks = data.get("content", [])
        if not isinstance(blocks, list):
            return ""
        parts: list[str] = []
        for b in cast(list[Any], blocks):
            if not isinstance(b, dict):
                continue
            bd = cast(dict[str, Any], b)
            if bd.get("type") != "text":
                continue
            t = bd.get("text", "")
            if isinstance(t, str):
                parts.append(t)
        return "".join(parts)

    def extract_usage_once(self, data: dict[str, Any]) -> dict[str, int]:
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return {"input_tokens": 0, "output_tokens": 0}
        u = cast(dict[str, Any], usage)
        return {
            "input_tokens": int(u.get("input_tokens", 0) or 0),
            "output_tokens": int(u.get("output_tokens", 0) or 0),
        }

    def extract_text_delta(self, event_name: str | None, data: dict[str, Any]) -> str:
        etype = event_name or data.get("type")
        if etype != "content_block_delta":
            return ""
        delta = data.get("delta")
        if not isinstance(delta, dict):
            return ""
        d = cast(dict[str, Any], delta)
        if d.get("type") != "text_delta":
            return ""
        text = d.get("text", "")
        return text if isinstance(text, str) else ""

    def update_usage(self, event_name: str | None, data: dict[str, Any], state: UsageState) -> None:
        etype = event_name or data.get("type")
        if etype == "message_start":
            msg = data.get("message")
            if isinstance(msg, dict):
                u = cast(dict[str, Any], msg).get("usage")
                if isinstance(u, dict):
                    ud = cast(dict[str, Any], u)
                    state.input_tokens = int(ud.get("input_tokens", 0) or 0)
                    # message_start 可能已带累计 output_tokens(通常 0 或 1)
                    state.output_tokens = int(ud.get("output_tokens", 0) or 0)
        elif etype == "message_delta":
            u = data.get("usage")
            if isinstance(u, dict):
                ud = cast(dict[str, Any], u)
                # Anthropic message_delta.usage.output_tokens 是累计值
                ot = ud.get("output_tokens")
                if isinstance(ot, int):
                    state.output_tokens = ot


# ================================================================
#  OpenAI Chat Completions /v1/chat/completions
# ================================================================


class CompletionsAdapter(ProtocolAdapter):
    def build_request_body(
        self, text: str, *, model: str, max_tokens: int, stream: bool
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": text}],
        }
        if stream:
            body["stream"] = True
            # 让最后一帧带累计 usage
            body["stream_options"] = {"include_usage": True}
            body["max_tokens"] = max_tokens
        return body

    def extract_text_once(self, data: dict[str, Any]) -> str:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first = cast(list[Any], choices)[0]
        if not isinstance(first, dict):
            return ""
        msg = cast(dict[str, Any], first).get("message")
        if not isinstance(msg, dict):
            return ""
        content = cast(dict[str, Any], msg).get("content")
        return content if isinstance(content, str) else ""

    def extract_usage_once(self, data: dict[str, Any]) -> dict[str, int]:
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return {"input_tokens": 0, "output_tokens": 0}
        u = cast(dict[str, Any], usage)
        return {
            "input_tokens": int(u.get("prompt_tokens", 0) or 0),
            "output_tokens": int(u.get("completion_tokens", 0) or 0),
        }

    def extract_text_delta(self, event_name: str | None, data: dict[str, Any]) -> str:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        choice = cast(list[Any], choices)[0]
        if not isinstance(choice, dict):
            return ""
        delta = cast(dict[str, Any], choice).get("delta")
        if not isinstance(delta, dict):
            return ""
        content = cast(dict[str, Any], delta).get("content")
        return content if isinstance(content, str) else ""

    def update_usage(self, event_name: str | None, data: dict[str, Any], state: UsageState) -> None:
        # Chat Completions 流在末尾的 chunk 里给 `usage`(需要 include_usage=true)
        u = data.get("usage")
        if isinstance(u, dict):
            ud = cast(dict[str, Any], u)
            state.input_tokens = int(ud.get("prompt_tokens", 0) or 0)
            state.output_tokens = int(ud.get("completion_tokens", 0) or 0)


# ================================================================
#  OpenAI Responses /v1/responses
# ================================================================


class ResponsesAdapter(ProtocolAdapter):
    def build_request_body(
        self, text: str, *, model: str, max_tokens: int, stream: bool
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "input": text,
        }
        if stream:
            body["stream"] = True
            body["max_output_tokens"] = max_tokens
        return body

    def extract_text_once(self, data: dict[str, Any]) -> str:
        output = data.get("output")
        if not isinstance(output, list):
            return ""
        parts: list[str] = []
        for item in cast(list[Any], output):
            if not isinstance(item, dict):
                continue
            it = cast(dict[str, Any], item)
            if it.get("type") != "message":
                continue
            content = it.get("content")
            if not isinstance(content, list):
                continue
            for c in cast(list[Any], content):
                if not isinstance(c, dict):
                    continue
                cd = cast(dict[str, Any], c)
                if cd.get("type") != "output_text":
                    continue
                t = cd.get("text", "")
                if isinstance(t, str):
                    parts.append(t)
        return "".join(parts)

    def extract_usage_once(self, data: dict[str, Any]) -> dict[str, int]:
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return {"input_tokens": 0, "output_tokens": 0}
        u = cast(dict[str, Any], usage)
        return {
            "input_tokens": int(u.get("input_tokens", 0) or 0),
            "output_tokens": int(u.get("output_tokens", 0) or 0),
        }

    def extract_text_delta(self, event_name: str | None, data: dict[str, Any]) -> str:
        etype = event_name or data.get("type")
        if etype != "response.output_text.delta":
            return ""
        delta = data.get("delta")
        return delta if isinstance(delta, str) else ""

    def update_usage(self, event_name: str | None, data: dict[str, Any], state: UsageState) -> None:
        etype = event_name or data.get("type")
        if etype != "response.completed":
            return
        resp = data.get("response")
        if not isinstance(resp, dict):
            return
        u = cast(dict[str, Any], resp).get("usage")
        if not isinstance(u, dict):
            return
        ud = cast(dict[str, Any], u)
        state.input_tokens = int(ud.get("input_tokens", 0) or 0)
        state.output_tokens = int(ud.get("output_tokens", 0) or 0)


# ================================================================
#  Dispatch 表 + 取 adapter 的入口
# ================================================================


_ADAPTERS: dict[Protocol, ProtocolAdapter] = {
    Protocol.MESSAGES: MessagesAdapter(),
    Protocol.CHAT_COMPLETIONS: CompletionsAdapter(),
    Protocol.RESPONSES: ResponsesAdapter(),
}


def adapter_for(fmt: Protocol) -> ProtocolAdapter:
    """按协议枚举取对应 adapter(单例)。"""
    return _ADAPTERS[fmt]

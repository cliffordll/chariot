"""AIAgent 输出 — `ChatEvent`(0.6.0+,跟 Claude SSE event 1:1)。

设计依据(详见 `docs/DESIGN.md` §3.2):
- `kind` 直接采用 Claude Messages API SSE event 命名,不做内部规范化重命名
- 字段对应 Claude payload(message / index / content_block / delta / usage /
  error_*)
- chariot 自注入两种 event(`tool_result` / `stream_done`),Claude 协议没有
  对应名,不冲突

10 种 kind:

| kind | 来源 | 说明 |
|------|------|------|
| message_start | Claude SSE | 一次 message 起头(message_id / model / 初始 usage) |
| content_block_start | Claude SSE | content block 开始(text / tool_use / thinking) |
| content_block_delta | Claude SSE | content block 增量(text_delta / input_json_delta / ...) |
| content_block_stop | Claude SSE | content block 收尾 |
| message_delta | Claude SSE | message 整体 delta(stop_reason + usage) |
| message_stop | Claude SSE | 一次 message 结束 |
| ping | Claude SSE | keepalive(消费方可忽略) |
| error | Claude SSE | 错误(error_type / error_message) |
| tool_result | chariot 自注 | 工具执行完(对应 user msg 里的 tool_result block) |
| stream_done | chariot 自注 | 整个 agent run 收敛(跨多轮 message_stop 后) |

字段全 optional,按 kind 取用。消费方用 `match event.kind` dispatch。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

# kind literal alias —— 静态分析时 dispatch 完整性靠这个
ChatEventKind = Literal[
    "message_start",
    "content_block_start",
    "content_block_delta",
    "content_block_stop",
    "message_delta",
    "message_stop",
    "ping",
    "error",
    "tool_result",
    "stream_done",
]


@dataclass(frozen=True)
class ChatEvent:
    """AIAgent / Provider 流式输出的事件单元。

    discriminated union by `kind`。所有专属字段都是 Optional(按 kind 取用),
    避免 ABC + 子类的 isinstance dispatch 树。

    序列化(给 sidecar JSON-RPC / 落库)用 `dataclasses.asdict(event)`,
    payload 字段名跟 Claude SSE 一致。

    工厂方法用 `@classmethod`,模块级零自由函数(CLAUDE.md ⭐)。
    """

    kind: ChatEventKind

    # ─── Claude SSE 原生字段 ───
    message: dict[str, Any] | None = None  # message_start
    index: int | None = None  # content_block_*
    content_block: dict[str, Any] | None = None  # content_block_start
    delta: dict[str, Any] | None = None  # content_block_delta / message_delta
    usage: dict[str, Any] | None = None  # message_start / message_delta
    error_type: str | None = None  # error
    error_message: str | None = None  # error

    # ─── tool_result 字段(对应 Claude tool_result content block) ───
    tool_use_id: str | None = None
    content: str | list[dict[str, Any]] | None = None
    is_error: bool = False

    # ---- 工厂方法(便于构造,避免到处填 None) ----

    @classmethod
    def message_start(
        cls,
        *,
        message_id: str,
        model: str,
        usage: dict[str, Any] | None = None,
    ) -> ChatEvent:
        """一次 message 起头(包 Claude `message_start` event)。"""
        msg: dict[str, Any] = {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": model,
            "stop_reason": None,
            "stop_sequence": None,
            "usage": usage or {},
        }
        return cls(kind="message_start", message=msg, usage=usage)

    @classmethod
    def text_block_start(cls, *, index: int) -> ChatEvent:
        """text content block 开始(`content_block_start`,type=text)。"""
        return cls(
            kind="content_block_start",
            index=index,
            content_block={"type": "text", "text": ""},
        )

    @classmethod
    def tool_use_block_start(
        cls,
        *,
        index: int,
        tool_use_id: str,
        tool_name: str,
    ) -> ChatEvent:
        """tool_use content block 开始(`content_block_start`,type=tool_use)。"""
        return cls(
            kind="content_block_start",
            index=index,
            content_block={
                "type": "tool_use",
                "id": tool_use_id,
                "name": tool_name,
                "input": {},
            },
        )

    @classmethod
    def text_delta(cls, text: str, *, index: int = 0) -> ChatEvent:
        """text 增量(`content_block_delta`,delta.type=text_delta)。"""
        return cls(
            kind="content_block_delta",
            index=index,
            delta={"type": "text_delta", "text": text},
        )

    @classmethod
    def input_json_delta(cls, partial_json: str, *, index: int) -> ChatEvent:
        """tool_use input JSON 增量(`content_block_delta`,delta.type=input_json_delta)。"""
        return cls(
            kind="content_block_delta",
            index=index,
            delta={"type": "input_json_delta", "partial_json": partial_json},
        )

    @classmethod
    def block_stop(cls, *, index: int) -> ChatEvent:
        """content block 收尾(`content_block_stop`)。"""
        return cls(kind="content_block_stop", index=index)

    @classmethod
    def message_delta_done(
        cls,
        *,
        stop_reason: str,
        stop_sequence: str | None = None,
        usage: dict[str, Any] | None = None,
    ) -> ChatEvent:
        """message 整体收尾 delta(`message_delta`,含 stop_reason + usage)。"""
        return cls(
            kind="message_delta",
            delta={"stop_reason": stop_reason, "stop_sequence": stop_sequence},
            usage=usage,
        )

    @classmethod
    def message_done(cls) -> ChatEvent:
        """一次 message 结束(`message_stop`,无附加字段)。"""
        return cls(kind="message_stop")

    @classmethod
    def ping_event(cls) -> ChatEvent:
        """keepalive(`ping`,消费方可忽略)。"""
        return cls(kind="ping")

    @classmethod
    def error_event(cls, *, error_type: str, error_message: str) -> ChatEvent:
        """错误事件。

        `error_type` 枚举(0.6.0):
        - upstream_auth_failed / upstream_unreachable / upstream_server_error
        - upstream_stream_error(200 已发后中途 IO 错)
        - rate_limited
        - unknown_provider(AIAgent 路由层;`req.provider` 在 providers dict 找不到)
        - agent_iter_exceeded(AgentLoop max_iter)
        - agent_timeout(AgentLoop 整体超时)
        - conversation_busy_local / conversation_busy_db(双层锁)
        - block_too_large(buffer 上限保护)
        """
        return cls(kind="error", error_type=error_type, error_message=error_message)

    @classmethod
    def tool_result_event(
        cls,
        *,
        tool_use_id: str,
        content: str | list[dict[str, Any]],
        is_error: bool = False,
    ) -> ChatEvent:
        """工具执行结果(chariot 自注;对应 Claude tool_result content block)。

        AgentLoop 在 message_stop 之后跑工具,每个工具产一个 tool_result event,
        然后拼下轮 req(把这个结果作为 user message 的 content block)。
        """
        return cls(
            kind="tool_result",
            tool_use_id=tool_use_id,
            content=content,
            is_error=is_error,
        )

    @classmethod
    def stream_done_event(cls) -> ChatEvent:
        """整个 agent run 收敛(chariot 自注;跨多轮 message_stop 后产)。

        AgentLoop 在 stop_reason ∈ {end_turn, max_tokens, stop_sequence} 时产;
        Provider 不产此事件。
        """
        return cls(kind="stream_done")

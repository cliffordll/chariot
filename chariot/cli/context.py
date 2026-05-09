"""chat 会话上下文 —— REPL / once / batch 共用的轻量协调器。

0.6.0 起持有 `AIAgent` 实例(进程内直调,撤 SDK ProxyClient),不走 HTTP / SSE。
`run_turn` 调 `agent.run_chat(req)` 消费 `ChatEvent` 流,本地累积 assistant 文本 +
usage 统计,把每个 event 透传给 caller 的 `on_event` 回调(REPL 下是
`Renderer.render_event`)。

stateful / stateless body.messages 契约
---------------------------------------
- **stateless**(`conversation_id is None`):req.messages = 整段本地历史。
  AIAgent 不持久化,Provider 看到的就是这里发的全部
- **stateful**(`conversation_id` 是 ULID):req.messages = **只发本轮新增 user
  message**(`self.messages[-1:]`)。AIAgent 内部 load DB 历史 prepend,把
  req.messages 里的 user 消息 append 到 messages 表(详见 `AIAgent._run_stateful_chat`)
  → 所以 client 必须只送"新增"的部分,否则会重复 persist

本地 `self.messages` 累积所有轮(给 REPL 失败 `pop_last` 回退用,以及 stateless
模式拼 req 用),但 stateful 模式下 `_messages_to_send` 只取末尾那条 user msg。

错误传播
--------
AIAgent / Provider 流里出错时 yield `ChatEvent(kind="error")`(不抛异常)。
`run_turn` 透传给 on_event 后,检测到 error event 就在流尾抛 `ChatError` —— 调用方
(REPL / once)用 `error_type` / `error_message` 渲染 error_bubble。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest, Message
from chariot.agent.run import AIAgent


def _empty_messages() -> list[dict[str, Any]]:
    """messages 字段的 default_factory;helper 显式标注类型避 pyright Unknown 推断。

    content 既可能是 str(本地 append 的)也可能是 anthropic content blocks 数组
    (DB 历史回放),用 Any 覆盖两种形态。
    """
    return []


@dataclass
class ChatError(Exception):
    """AIAgent 流里收到 `error` event 时 `run_turn` 抛出的异常。"""

    error_type: str
    error_message: str

    def short_message(self, limit: int = 200) -> str:
        s = self.error_message.strip()
        return s if len(s) <= limit else s[:limit] + "…"


@dataclass(frozen=True)
class TurnResult:
    """一轮请求的收尾结果(ChatRepl / ChatOnce 用 meta 行展示)。"""

    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


@dataclass
class ChatContext:
    """一次聊天会话的上下文:AIAgent 引用 + 会话配置 + 多轮历史。"""

    agent: AIAgent
    # provider entry name(0.6.0 v6 起):chariot 路由 key,对应 DB providers
    # 表的 entry name。透传给 ChatRequest.provider_name(0.6.5+ 起字段名跟
    # wire `model` 区分开)。
    provider_name: str = ""
    max_tokens: int = 1024
    messages: list[dict[str, Any]] = field(default_factory=_empty_messages)
    # 0.4.0:可选 conversation id(ULID)。给了则 ChatRequest.conversation_id 透传给 AIAgent,
    # 走 stateful 路径(DB load history + persist new turn)。CLI 本地
    # self.messages 仍累积本进程内的轮(便于 REPL 打印 / 撤回);发请求时
    # stateful 模式只送"这一轮新增"避免双 persist
    conversation_id: str | None = None
    # 0.6.5+:CLI `--model` flag 的承载;每轮 req 透传给 `ChatRequest.model`,
    # Provider 内部用 `req.model or self.config.model` 决定 wire body["model"]。
    # None = 不覆盖,沿用 entry.options.model(常态)
    model_override: str | None = None

    # ---------- 状态操作 ----------

    def append_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def append_assistant(self, text: str) -> None:
        self.messages.append({"role": "assistant", "content": text})

    def pop_last(self) -> None:
        """撤回最后一条消息;REPL 本轮请求失败时用,避免污染后续上下文。"""
        if self.messages:
            self.messages.pop()

    def reset(self) -> None:
        """清空对话历史,保留会话配置(provider_name / max_tokens / conversation_id)。"""
        self.messages.clear()

    def set_provider(self, name: str) -> None:
        self.provider_name = name

    # ---------- 核心:一轮请求 ----------

    async def run_turn(self, on_event: Callable[[ChatEvent], None]) -> TurnResult:
        """构造 ChatRequest → 调 `agent.run_chat(req)` → 流式 ChatEvent → 收尾。

        - 每个 ChatEvent 透传给 `on_event`(REPL 下是 `Renderer.render_event`)
        - 内部累积 assistant 文本(只取最末轮 message_stop 前的 text_delta)
        - 累积 usage 字段(message_start.input_tokens + message_delta.output_tokens)
        - 流里出现 `kind="error"` event → 流结束后抛 `ChatError`

        TurnResult.text 取**最后一个 message 的 text 块**(stop_reason=end_turn 那轮),
        中间轮(stop_reason=tool_use)的 text 算"过程文本",用户已经通过 on_event
        看到流式输出,不进 TurnResult。
        """
        req = self._build_request()
        current_text: list[str] = []
        last_message_text: list[str] = []
        input_tokens = 0
        output_tokens = 0
        error_event: ChatEvent | None = None
        t0 = time.monotonic()

        async for ev in self.agent.run_chat(req):
            on_event(ev)
            self._accumulate_text(ev, current_text)
            if ev.kind == "message_stop":
                # 一个 message 收尾 —— snapshot;下一轮(若有)从空起
                last_message_text = current_text
                current_text = []
            input_tokens, output_tokens = self._accumulate_usage(ev, input_tokens, output_tokens)
            if ev.kind == "error":
                error_event = ev

        if error_event is not None:
            raise ChatError(
                error_type=error_event.error_type or "unknown",
                error_message=error_event.error_message or "",
            )

        return TurnResult(
            text="".join(last_message_text),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    @staticmethod
    def _accumulate_text(ev: ChatEvent, sink: list[str]) -> None:
        """从 content_block_delta(text_delta)抽 text 累到 sink。"""
        if ev.kind != "content_block_delta":
            return
        delta = ev.delta or {}
        if delta.get("type") == "text_delta":
            sink.append(str(delta.get("text", "")))

    @staticmethod
    def _accumulate_usage(ev: ChatEvent, input_tokens: int, output_tokens: int) -> tuple[int, int]:
        """从 message_start / message_delta 收 usage 字段(累加,跨轮各占一份)。"""
        if ev.kind == "message_start" and ev.usage:
            input_tokens += int(ev.usage.get("input_tokens") or 0)
            output_tokens += int(ev.usage.get("output_tokens") or 0)
        elif ev.kind == "message_delta" and ev.usage:
            output_tokens += int(ev.usage.get("output_tokens") or 0)
        return input_tokens, output_tokens

    # ---------- 私有:组装 ChatRequest ----------

    def _build_request(self) -> ChatRequest:
        """把对话历史组装成 ChatRequest。

        req.messages 取值取决于 stateful / stateless;详见模块级 docstring 契约段。
        `ChatRequest.provider_name` 是 chariot 路由 key(entry name);wire 字段
        `body.model` 由 Provider 内部从 `req.model or self.config.model` 决定
        —— `model_override` 非 None 时走 per-call 覆盖(CLI `--model`),
        否则用 entry.options.model。
        """
        return ChatRequest(
            provider_name=self.provider_name,
            messages=[self._to_message(m) for m in self._messages_to_send()],
            model=self.model_override,
            max_tokens=self.max_tokens,
            conversation_id=self.conversation_id,
        )

    def _messages_to_send(self) -> list[dict[str, Any]]:
        """决定 req.messages 装什么。

        - stateful(有 conversation_id):只发末尾那条(本轮新增 user msg);AIAgent
          自己从 DB prepend 历史。这条规则避免 AIAgent 把已 persist 的老消息
          重复 append(详见模块 docstring 契约段)
        - stateless:发全量本地历史,AIAgent 不持久化
        """
        if self.conversation_id is not None:
            return self.messages[-1:] if self.messages else []
        return list(self.messages)

    @staticmethod
    def _to_message(raw: dict[str, Any]) -> Message:
        """本地 dict({role, content})→ frozen `Message`。"""
        role = raw["role"]
        if role not in ("user", "assistant"):
            raise ValueError(f"unsupported role in local messages: {role!r}")
        return Message(role=role, content=raw["content"])

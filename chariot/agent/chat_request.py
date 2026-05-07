"""AIAgent 输入 — `ChatRequest`(0.6.0+,跟 Claude Messages API 1:1 平铺)。

设计依据(详见 `docs/DESIGN.md` §3.1):
- 跟 Claude Messages API request body schema **1:1 平铺**
- 字段命名 / 类型 / 默认值都对齐 Claude
- AnthropicProvider 几乎透传:`dataclasses.asdict(req) → httpx.post(json=...)`
- Claude API 用户能直接 `ChatRequest(**claude_body)` 把现有调用代码搬过来

关于 chariot 扩展字段:
- `conversation_id`:0.4.0 起 stateful 多轮触发(0.6.0 起从
  `X-Chariot-Conversation` header 升级到顶层字段)
- `agent_id`:0.9.0+ 多 AIAgent 实例路由;0.6.0 默认 `None`,字段先占位

不引入的 Claude 字段:
- `stream`:chariot 内核固定流式(`Provider.generate` 总是 `AsyncIterator`)
- `service_tier`:Anthropic 计费层级,在 Provider options 里配
- `anthropic_version` / `anthropic_beta`:HTTP header 级,Provider 内部处理
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class Message:
    """一条消息;Claude 形态(content 是字符串简写或 content block list)。

    `role` 只能是 user / assistant —— Claude 协议的 system 是 ChatRequest
    顶层字段,不是消息;tool_result 是 user message 里的 content block,
    不是单独 role(跟 OpenAI 形态不同,详见 DESIGN §7.1)。

    `content`:
    - `str`:字符串简写,等价于 `[{"type": "text", "text": "..."}]`
    - `list[dict]`:Claude content blocks,每个 dict 含 `type` 字段
      (text / image / tool_use / tool_result / thinking / document 等),
      此处用 `dict[str, Any]` 而非具体子类型 —— Claude content block schema
      演化频繁,strict typing 会绊住扩展;反正最终是 `json.dumps` 成 wire
    """

    role: Literal["user", "assistant"]
    content: str | list[dict[str, Any]]


@dataclass(frozen=True)
class SystemBlock:
    """system prompt 的 content block 形态(Claude 支持多个 block + 缓存控制)。

    简单场景下 `ChatRequest.system` 用 `str`(单段);多段或要 `cache_control`
    时用 `list[SystemBlock]`。

    `cache_control`:`{"type": "ephemeral"}` 触发 prompt 缓存(Claude 特性)。
    """

    type: Literal["text"]
    text: str
    cache_control: dict[str, Any] | None = None


@dataclass(frozen=True)
class ToolSchema:
    """tool 描述(给 LLM 看,不是 Tool 实现)。

    跟 Claude tool schema 1:1。`input_schema` 是 JSON Schema,描述工具
    参数;LLM 据此决定调不调 + 怎么填参数。
    """

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ChatRequest:
    """AIAgent 主入口的输入 —— 跟 Claude Messages API request body 1:1。

    14 字段平铺:12 个跟 Claude API 同名同义,2 个 chariot 扩展。
    顺序按 Claude 官方 spec(model / messages / max_tokens / system / tools
    / tool_choice / 各 sampling / metadata / thinking)+ chariot 扩展放最后。

    `model` 字段语义在 chariot 层做了一层抽象:Claude API 里 `model` 是 LLM
    模型 ID(如 `claude-sonnet-4-6`);chariot 这里 `model` 是 entry name
    (用户在 `models` 表里的命名,如 `claude` / `mock` / `gpt-4`),由 AIAgent
    路由到对应 Provider 实例,Provider 内部把真实的 model ID 传给上游。

    `messages` 是必填(Claude API 要求);其它字段都有合理默认。
    """

    # ─── Claude Messages API 字段(顺序按官方 spec) ───
    model: str  # entry name(chariot 内部当 Provider 路由)
    messages: list[Message]
    max_tokens: int = 4096
    system: str | list[SystemBlock] | None = None
    tools: list[ToolSchema] | None = None  # None = 沿用 enabled tools 注入;[] = 关闭工具调用
    tool_choice: dict[str, Any] | None = None  # {"type": "auto"|"any"|"tool"|"none"}
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    stop_sequences: list[str] | None = None
    metadata: dict[str, Any] | None = None  # {"user_id": "..."}
    thinking: dict[str, Any] | None = None  # extended thinking 配置(Claude 4+)

    # ─── chariot 扩展字段(顶层放,跟 Claude 字段不冲突) ───
    conversation_id: str | None = None  # None = stateless;ULID = stateful
    agent_id: str | None = None  # 0.9.0+ 多 AIAgent 实例;0.6.0 默认 None

    # ---- 查询便利方法(逻辑收进类,不散成模块级 helper) ----

    def is_stateful(self) -> bool:
        """是否带 conversation_id(stateful 多轮)。"""
        return self.conversation_id is not None

    def last_user_text(self) -> str | None:
        """末轮 user message 的纯文本(若 content 是字符串或全 text block);
        否则返 None。给 mock provider / 简单用例做 echo 用。
        """
        for msg in reversed(self.messages):
            if msg.role != "user":
                continue
            if isinstance(msg.content, str):
                return msg.content
            texts = [b["text"] for b in msg.content if b.get("type") == "text"]
            return "".join(texts) if texts else None
        return None

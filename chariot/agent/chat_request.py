"""AIAgent 输入 — `ChatRequest`(0.6.0+,结构跟 Claude Messages API 1:1 对齐)。

设计依据(详见 `docs/DESIGN.md` §3.1):
- **结构**(`messages.content`、tool_use / tool_result block 嵌套形式、role 词表)
  跟 Claude Messages API request body 1:1
- 字段类型 / 默认值都对齐 Claude
- 命名上有一处偏离:**chariot 用 `provider_name` 字段做路由 key**(对应 DB
  `providers` 表的 entry name;CLI flag 是 `--provider`,IR / 内部参数用
  `provider_name`,长名避免跟 wire 字段歧义);Claude wire 字段名是 `model`
  (LLM id),由 AnthropicProvider 在 `_build_body` 里写死 `body["model"]
  = self.config.model`
- Provider 内部都得做 entry name → wire 字段的最后一跳翻译,这步不可省

关于 chariot 扩展字段:
- `provider_name`:必填,路由 key(从 `--provider` flag 或 DB 默认 entry 来)
- `conversation_id`:0.4.0 起 stateful 多轮触发(0.6.0 起从
  `X-Chariot-Conversation` header 升级到顶层字段)
- `agent_id`:0.9.0+ 多 AIAgent 实例路由;0.6.0 默认 `None`,字段先占位

不引入的 Claude 字段:
- `stream`:chariot 内核固定流式(`Provider.generate` 总是 `AsyncIterator`)
- `service_tier`:Anthropic 计费层级,在 Provider options 里配
- `anthropic_version` / `anthropic_beta`:HTTP header 级,Provider 内部处理

`model` 字段(0.6.5+):
- chariot IR 主路由 key 是 `provider_name`(对应 DB entry name);wire body
  里仍要写 `model`,Provider 内部从 `req.model or self.config.model` 决定
- per-call 用法:`req.model = "claude-haiku-4-5"` → AnthropicProvider 把它写进
  body["model"];默认 None = 用 entry.options.model(实例化时落到 self.config.model)
- 设计动机:`--model` 切 LLM id 不影响 (base_url, api_key) → 不重建 httpx client
  → 零客户端开销;走 ChatRequest 字段而非 provider_overrides(后者会重建 Provider)
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
    """AIAgent 主入口的输入 —— 结构跟 Claude Messages API request body 对齐;
    路由字段命名按 chariot 自己的语义(`provider_name` 而非 wire 字段名 `model`)。

    15 字段平铺:12 个跟 Claude API 同名同义(含 0.6.5+ 加回的 `model`),
    3 个 chariot 扩展(provider_name / conversation_id / agent_id)。顺序按 Claude
    官方 spec(model / messages / max_tokens / system / tools / tool_choice /
    各 sampling / metadata / thinking)+ chariot 扩展放最后(`provider_name`
    必填字段排最前)。

    `provider_name` 字段是 chariot 的路由 key:对应 DB `providers` 表里的 entry
    name(如 `claude` / `mock` / `ollama-qwen`),由 AIAgent 路由到对应
    `BaseProvider` 实例。`model`(0.6.5+,可选)是 per-call LLM id 覆盖;
    Provider 内部按 `req.model or self.config.model` 决定 wire body["model"]。
    **`req.provider_name`(路由 key)≠ `req.model`(wire LLM id)**。

    命名约定:CLI flag 用短名 `--provider` / `--model`(贴近用户)、IR / 内部
    参数传递用 `provider_name` / `model`。

    `messages` 是必填(Claude API 要求);其它字段都有合理默认。
    """

    # ─── chariot 路由字段(必填) ───
    provider_name: str  # entry name(chariot 内部当 Provider 路由 key)

    # ─── Claude Messages API 字段(顺序按官方 spec) ───
    messages: list[Message]
    model: str | None = None  # 0.6.5+ per-call LLM id 覆盖;None = 用 entry.options.model
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
    agent_profile: str | None = None
    """0.7.2-tool+ AgentProfile name 引用;非 None 时 AIAgent 解析后:
    - profile.provider_profile 覆盖 req.provider_name
    - profile.prompt_bundle 决定 prompt 注入(取代 get_active_bundle 兜底)
    - profile.tool_profile 决定 toolset filter(取代全量挂载 tools)
    dangling reference(profile name 不存在)走 fallback,不阻断 task。
    """

    reflection_enabled: bool = False
    """B4 wave 2:是否走 reflect-then-retry。需 AIAgent 装载了 critic
    (`auxiliary_clients.name='critic'`)才生效;否则字段被忽略。
    wave 3 起从 agent_profile.reflection_enabled 透传。
    """

    reflection_max_retries: int = 2
    """B4 wave 2:reflection 最多 retry 几次;默认 2(初次跑 + 至多 2 次 retry)。"""

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

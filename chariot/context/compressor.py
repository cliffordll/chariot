"""ContextCompressor —— 长对话自动压缩(B3 wave 2,主动 rewrite)。

触发条件:`prompt_tokens` 估值 / `context_length` > `threshold` 时,把 messages
里"最早 N turn"换成一条 `[context-summary] ...` 的 user message,保留 system 头
和近期对话。摘要由 `AuxiliaryClient` 跑;失败 fallback 到 oldest-pair pruning
(直接丢最早 2 条 user+assistant)。

**职责边界**:Compressor **改写 ChatRequest.messages**(给 provider 看的是压缩
后的版本);要被动记录 context 长什么样的逻辑在 `ContextComposer`
(`composer.py`)。两者协作:Composer 先 snapshot 原貌,Compressor 再压缩送给
provider —— trace 里能同时看到原始 context + 压缩后的 prompt。

Token 估算策略:不引入 tiktoken(额外 dep + Anthropic / OpenAI tokenization 不同步);
用 char-level 粗估 `len(text) // 3`(中文 ~3 char/token,英文 ~4 char/token,折中)。
准确度差 ~30%,但够触发判断;后续可接 provider-specific token counter API。

模块级零自由函数;所有逻辑收进 `ContextCompressor` 类。
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from chariot.agent.chat_request import ChatRequest, Message, SystemBlock
from chariot.providers.auxiliary_client import AuxiliarySummarizeFailed

if TYPE_CHECKING:
    from chariot.providers.auxiliary_client import AuxiliaryClient


_DEFAULT_CONTEXT_LENGTH = 8192


@dataclass(frozen=True)
class CompressionResult:
    """压缩结果(便于 agent 写 trace.meta)。"""

    compressed: bool
    """是否实际压缩了(False 表示低于 threshold 或 messages 不够多)。"""

    strategy: str
    """'summary' / 'oldest_pair_pruning' / 'noop'。"""

    summary_text: str | None
    """摘要文本(strategy='summary' 时非空)。"""

    dropped_turns: int
    """从 head 切掉的 turn 数(strategy='summary' 摘要进 1 条,oldest_pair 切 2 条)。"""

    prompt_tokens_estimate: int
    """触发判断时估算的 prompt token 数。"""


class ContextCompressor:
    """长对话压缩器。

    用法::

        compressor = ContextCompressor(aux_client=summarizer)
        new_req, result = await compressor.maybe_compress(req, context_length=8192)
        # new_req 是改写后的 ChatRequest;result 描述发生了什么(写 trace 用)
    """

    def __init__(
        self,
        aux_client: AuxiliaryClient,
        *,
        threshold: float = 0.7,
        summary_turns: int = 5,
    ) -> None:
        if not 0.0 < threshold <= 1.0:
            raise ValueError(f"threshold 必须在 (0, 1] 区间,got {threshold}")
        if summary_turns < 1:
            raise ValueError(f"summary_turns 必须 >= 1,got {summary_turns}")
        self._aux = aux_client
        self._threshold = threshold
        self._summary_turns = summary_turns

    async def maybe_compress(
        self,
        req: ChatRequest,
        *,
        context_length: int = _DEFAULT_CONTEXT_LENGTH,
    ) -> tuple[ChatRequest, CompressionResult]:
        """估 prompt_tokens / context_length;过 threshold → 压缩;否则 noop。

        压缩失败(aux call 抛 `AuxiliarySummarizeFailed`)→ fallback oldest-pair
        pruning(只切最早 1 个 user+assistant pair,保守不丢太多)。
        """
        tokens_est = self.estimate_prompt_tokens(req)
        ratio = tokens_est / max(context_length, 1)
        if ratio <= self._threshold:
            return req, CompressionResult(
                compressed=False,
                strategy="noop",
                summary_text=None,
                dropped_turns=0,
                prompt_tokens_estimate=tokens_est,
            )

        head, tail = self._split_messages(req.messages, self._summary_turns)
        if not head:
            return req, CompressionResult(
                compressed=False,
                strategy="noop",
                summary_text=None,
                dropped_turns=0,
                prompt_tokens_estimate=tokens_est,
            )

        head_text = self._serialize_messages_to_text(head)
        try:
            summary = await self._aux.summarize(head_text)
        except AuxiliarySummarizeFailed:
            pruned = self._oldest_pair_pruning(req.messages)
            if pruned is None:
                return req, CompressionResult(
                    compressed=False,
                    strategy="noop",
                    summary_text=None,
                    dropped_turns=0,
                    prompt_tokens_estimate=tokens_est,
                )
            new_messages, dropped = pruned
            return dataclasses.replace(req, messages=new_messages), CompressionResult(
                compressed=True,
                strategy="oldest_pair_pruning",
                summary_text=None,
                dropped_turns=dropped,
                prompt_tokens_estimate=tokens_est,
            )

        summary_msg = Message(role="user", content=f"[context-summary] {summary}")
        new_messages = [summary_msg, *tail]
        return dataclasses.replace(req, messages=new_messages), CompressionResult(
            compressed=True,
            strategy="summary",
            summary_text=summary,
            dropped_turns=len(head),
            prompt_tokens_estimate=tokens_est,
        )

    # ---- token estimate ----

    @classmethod
    def estimate_prompt_tokens(cls, req: ChatRequest) -> int:
        """粗估 prompt tokens(system + messages 全文)。

        近似:`len(text) // 3`(中文 ~3 char/token,英文 ~4 char/token 折中)。
        """
        total = 0
        if req.system is not None:
            total += cls._estimate_text_tokens(cls._system_to_text(req.system))
        for msg in req.messages:
            total += cls._estimate_text_tokens(cls._message_to_text(msg))
        return total

    @staticmethod
    def _estimate_text_tokens(text: str) -> int:
        return max(len(text) // 3, 1) if text else 0

    @staticmethod
    def _system_to_text(system: str | list[SystemBlock]) -> str:
        if isinstance(system, str):
            return system
        return "\n".join(b.text for b in system)

    @classmethod
    def _message_to_text(cls, msg: Message) -> str:
        if isinstance(msg.content, str):
            return msg.content
        parts: list[str] = []
        for block in msg.content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                parts.append(str(block.get("text", "")))
            elif btype == "tool_use":
                parts.append(json.dumps(block.get("input", {}), ensure_ascii=False))
            elif btype == "tool_result":
                inner = block.get("content")
                if isinstance(inner, str):
                    parts.append(inner)
                elif isinstance(inner, list):
                    for ib in inner:
                        if isinstance(ib, dict) and ib.get("type") == "text":
                            parts.append(str(ib.get("text", "")))
        return "\n".join(parts)

    # ---- 切分策略 ----

    @staticmethod
    def _split_messages(messages: list[Message], summary_turns: int) -> tuple[list[Message], list[Message]]:
        """切 head(待摘要)/ tail(保留)。

        约定:1 turn = 1 user + 1 assistant(连续 2 条);最后 2 条强制保留。
        """
        if len(messages) <= 2:
            return [], list(messages)
        head_count = min(summary_turns * 2, max(len(messages) - 2, 0))
        # 切到 user 边界(避免开头是 assistant)
        if head_count > 0 and messages[head_count - 1].role == "user":
            head_count -= 1
        return list(messages[:head_count]), list(messages[head_count:])

    @classmethod
    def _serialize_messages_to_text(cls, messages: list[Message]) -> str:
        lines: list[str] = []
        for msg in messages:
            text = cls._message_to_text(msg)
            if not text:
                continue
            lines.append(f"{msg.role}: {text}")
        return "\n\n".join(lines)

    @staticmethod
    def _oldest_pair_pruning(
        messages: list[Message],
    ) -> tuple[list[Message], int] | None:
        """从 head 切掉最早 1 个 user+assistant pair;不足则 None。"""
        if len(messages) < 4:
            return None
        # 找第一个 user → assistant 紧邻 pair
        for i in range(len(messages) - 1):
            if messages[i].role == "user" and messages[i + 1].role == "assistant":
                return list(messages[i + 2 :]), 2
        return None

    def _provided_aux_meta(self) -> dict[str, Any]:
        return {"aux_client": self._aux.name}

"""AuxiliaryClient —— 副 model wrapper(B3 wave 2)。

设计动机:Context auto-compression 需要"调一个独立路由的 LLM 做摘要",但又不想
新增 BaseProvider 子类。`AuxiliaryClient` 是 entry-level 的"指向某个 provider entry
+ 独立 model id + 独立 sampling params" 包装,内部仍走现有 ProviderRegistry。

封装策略(CLAUDE.md ⭐ 封装与内聚最高优先级):
- `AuxiliaryClient` 持 entry + 已构造的 BaseProvider 实例
- `summarize(text)` 方法直接对外暴露,内部组装 `ChatRequest` 跑 provider.generate
- 不暴露底层 ChatEvent;summarize 返纯字符串(收敛 + 简化测试)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest, Message

if TYPE_CHECKING:
    from chariot.models.auxiliary import AuxiliaryClientEntry
    from chariot.providers.base import BaseProvider


class AuxiliarySummarizeFailed(Exception):  # noqa: N818 — 短名对调用方更友好
    """副 model summarize 调用失败(provider 报错 / 空返回)。

    `ContextCompressor.maybe_compress` 捕获后走 fallback oldest-pair pruning。
    """


class AuxiliaryClient:
    """副 model wrapper:绑定一个已构造的 BaseProvider 实例 + entry-level 默认 params。

    用法::

        aux = AuxiliaryClient(entry=aux_entry, provider=mock_provider)
        summary = await aux.summarize("把这段对话摘要成一句话:...")
    """

    SUMMARIZE_PROMPT = (
        "你是上下文摘要助手。把下面的对话片段总结成 1-3 句中文,"
        "保留关键事实 / 用户意图 / 已下的决定;不要复述,不要列要点。\n\n"
    )

    def __init__(self, entry: AuxiliaryClientEntry, provider: BaseProvider) -> None:
        self._entry = entry
        self._provider = provider

    @property
    def name(self) -> str:
        return self._entry.name

    @property
    def entry(self) -> AuxiliaryClientEntry:
        return self._entry

    async def summarize(self, text: str) -> str:
        """跑一次摘要;聚合 text_delta event 成单字符串返回。

        失败语义:`ChatEvent(kind='error')` 或空输出 → 抛 `AuxiliarySummarizeFailed`。
        """
        if not text.strip():
            return ""
        req = self._build_request(text)
        chunks: list[str] = []
        async for ev in self._provider.generate(req):
            if ev.kind == "error":
                raise AuxiliarySummarizeFailed(
                    f"auxiliary {self._entry.name!r} 失败: {ev.error_type}: {ev.error_message}"
                )
            chunks.append(self._extract_text_chunk(ev))
        result = "".join(chunks).strip()
        if not result:
            raise AuxiliarySummarizeFailed(f"auxiliary {self._entry.name!r} 返回空输出")
        return result

    def _build_request(self, text: str) -> ChatRequest:
        params = self._entry.params
        max_tokens = int(params.get("max_tokens", 512))
        temperature = params.get("temperature")
        top_p = params.get("top_p")
        model = self._entry.model or self._provider.config.model
        return ChatRequest(
            provider_name=self._entry.provider_entry,
            messages=[Message(role="user", content=self.SUMMARIZE_PROMPT + text)],
            model=model,
            max_tokens=max_tokens,
            temperature=temperature if isinstance(temperature, int | float) else None,
            top_p=top_p if isinstance(top_p, int | float) else None,
        )

    @staticmethod
    def _extract_text_chunk(ev: ChatEvent) -> str:
        if ev.kind != "content_block_delta":
            return ""
        delta = ev.delta or {}
        if delta.get("type") != "text_delta":
            return ""
        text = delta.get("text", "")
        return text if isinstance(text, str) else ""

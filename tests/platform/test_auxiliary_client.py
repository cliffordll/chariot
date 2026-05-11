"""B3 wave 2 — `AuxiliaryClient.summarize` 跑 MockProvider 聚合 text_delta。"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest
from chariot.models.auxiliary import AuxiliaryClientEntry
from chariot.providers.auxiliary_client import AuxiliaryClient, AuxiliarySummarizeFailed
from chariot.providers.base import BaseProvider, BaseProviderConfig
from chariot.providers.builtin.mock import MockProvider


class _ErroringProvider(BaseProvider):
    """200 后立刻 yield error event。"""

    capabilities = MockProvider.capabilities

    def __init__(self) -> None:
        self.config = BaseProviderConfig(name="err", model="err-1")

    @classmethod
    def create(cls, options: dict[str, object]) -> _ErroringProvider:
        return cls()

    async def generate(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        yield ChatEvent.error_event(error_type="upstream_stream_error", error_message="boom")


async def test_summarize_returns_text_from_mock_provider() -> None:
    """MockProvider echo 末轮 user content;summarize 应聚合 [mock] echo: <text>。"""
    aux = AuxiliaryClient(
        entry=AuxiliaryClientEntry(name="summarizer", provider_entry="mock"),
        provider=MockProvider.create({}),
    )
    result = await aux.summarize("hello world")
    assert "hello world" in result
    assert "[mock]" in result


async def test_summarize_empty_input_returns_empty() -> None:
    aux = AuxiliaryClient(
        entry=AuxiliaryClientEntry(name="summarizer", provider_entry="mock"),
        provider=MockProvider.create({}),
    )
    assert await aux.summarize("") == ""
    assert await aux.summarize("   \n  ") == ""


async def test_summarize_raises_on_provider_error() -> None:
    aux = AuxiliaryClient(
        entry=AuxiliaryClientEntry(name="summarizer", provider_entry="err"),
        provider=_ErroringProvider(),
    )
    with pytest.raises(AuxiliarySummarizeFailed):
        await aux.summarize("anything")


async def test_summarize_respects_max_tokens_param() -> None:
    """params.max_tokens 透传到 ChatRequest;mock 不 enforce 但应不报错。"""
    aux = AuxiliaryClient(
        entry=AuxiliaryClientEntry(
            name="summarizer",
            provider_entry="mock",
            params={"max_tokens": 32},
        ),
        provider=MockProvider.create({}),
    )
    result = await aux.summarize("xyz")
    assert "xyz" in result

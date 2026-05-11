"""B3 wave 2 — `ContextCompressor` 触发阈值 / summary / fallback / token 估算。"""

from __future__ import annotations

import pytest

from chariot.agent.auxiliary_client import AuxiliaryClient, AuxiliarySummarizeFailed
from chariot.agent.chat_request import ChatRequest, Message
from chariot.context.compressor import CompressionResult, ContextCompressor
from chariot.models.auxiliary import AuxiliaryClientEntry


class _StubAuxOk(AuxiliaryClient):
    """跳过 BaseProvider,直接返固定摘要 — 不调用 super().__init__。"""

    def __init__(self, *, summary: str = "STUB_SUMMARY") -> None:
        self._entry = AuxiliaryClientEntry(name="stub", provider_entry="mock")
        self._summary = summary

    async def summarize(self, text: str) -> str:
        return self._summary

    @property
    def name(self) -> str:
        return self._entry.name


class _StubAuxFail(AuxiliaryClient):
    def __init__(self) -> None:
        self._entry = AuxiliaryClientEntry(name="stub_fail", provider_entry="mock")

    async def summarize(self, text: str) -> str:
        raise AuxiliarySummarizeFailed("stub fail")

    @property
    def name(self) -> str:
        return self._entry.name


def _build_long_request(turns: int) -> ChatRequest:
    """构造 `turns` 个 user/assistant pair,每条 ~600 char(>= ~200 tokens)。"""
    big = "x" * 600
    msgs: list[Message] = []
    for i in range(turns):
        msgs.append(Message(role="user", content=f"user_{i} {big}"))
        msgs.append(Message(role="assistant", content=f"assistant_{i} {big}"))
    return ChatRequest(provider_name="mock", messages=msgs)


# ---- token 估算 ----


def test_estimate_prompt_tokens_string_messages() -> None:
    req = ChatRequest(provider_name="mock", messages=[Message(role="user", content="abc")])
    # len(text)//3 = 1
    assert ContextCompressor.estimate_prompt_tokens(req) == 1


def test_estimate_prompt_tokens_with_system() -> None:
    req = ChatRequest(
        provider_name="mock",
        messages=[Message(role="user", content="abc")],
        system="hello world",  # 11 // 3 = 3
    )
    assert ContextCompressor.estimate_prompt_tokens(req) >= 4


def test_estimate_handles_anthropic_blocks() -> None:
    req = ChatRequest(
        provider_name="mock",
        messages=[
            Message(
                role="assistant",
                content=[
                    {"type": "text", "text": "hello"},
                    {"type": "tool_use", "id": "t1", "name": "read", "input": {"path": "x.md"}},
                ],
            ),
        ],
    )
    # text "hello" + tool_use.input JSON
    assert ContextCompressor.estimate_prompt_tokens(req) > 0


# ---- maybe_compress ----


async def test_noop_when_under_threshold() -> None:
    compressor = ContextCompressor(_StubAuxOk(), threshold=0.7, summary_turns=2)
    req = ChatRequest(provider_name="mock", messages=[Message(role="user", content="short")])
    new_req, result = await compressor.maybe_compress(req, context_length=8192)
    assert new_req is req
    assert result.compressed is False
    assert result.strategy == "noop"


async def test_summary_path_replaces_head() -> None:
    """长对话(超 threshold)→ aux.summarize → 替换 head 为 [context-summary] msg。"""
    compressor = ContextCompressor(_StubAuxOk(summary="OLD STUFF"), threshold=0.5, summary_turns=2)
    req = _build_long_request(turns=8)  # 16 messages
    new_req, result = await compressor.maybe_compress(req, context_length=200)
    assert result.compressed is True
    assert result.strategy == "summary"
    assert result.summary_text == "OLD STUFF"
    # 第一条是 summary marker
    first = new_req.messages[0]
    assert first.role == "user"
    assert isinstance(first.content, str)
    assert first.content.startswith("[context-summary]")
    # 尾部保留近期对话(>= 最后 2 条)
    assert len(new_req.messages) < len(req.messages)
    assert new_req.messages[-1].role == req.messages[-1].role


async def test_fallback_to_oldest_pair_pruning_on_aux_failure() -> None:
    compressor = ContextCompressor(_StubAuxFail(), threshold=0.5, summary_turns=2)
    req = _build_long_request(turns=8)
    new_req, result = await compressor.maybe_compress(req, context_length=200)
    assert result.compressed is True
    assert result.strategy == "oldest_pair_pruning"
    assert result.summary_text is None
    assert result.dropped_turns == 2
    assert len(new_req.messages) == len(req.messages) - 2


async def test_compression_preserves_request_fields() -> None:
    """触发压缩后,非 messages 字段(provider_name / model 等)原样保留。"""
    compressor = ContextCompressor(_StubAuxOk(), threshold=0.5, summary_turns=2)
    req = ChatRequest(
        provider_name="mock",
        model="mock-1",
        max_tokens=1024,
        messages=_build_long_request(turns=8).messages,
    )
    new_req, _ = await compressor.maybe_compress(req, context_length=200)
    assert new_req.provider_name == "mock"
    assert new_req.model == "mock-1"
    assert new_req.max_tokens == 1024


def test_invalid_threshold_raises() -> None:
    with pytest.raises(ValueError):
        ContextCompressor(_StubAuxOk(), threshold=0.0)
    with pytest.raises(ValueError):
        ContextCompressor(_StubAuxOk(), threshold=1.5)


def test_invalid_summary_turns_raises() -> None:
    with pytest.raises(ValueError):
        ContextCompressor(_StubAuxOk(), summary_turns=0)


# ---- CompressionResult 字段 ----


def test_compression_result_is_frozen_dataclass() -> None:
    r = CompressionResult(
        compressed=True,
        strategy="summary",
        summary_text="x",
        dropped_turns=4,
        prompt_tokens_estimate=1000,
    )
    assert r.strategy == "summary"
    with pytest.raises(AttributeError):
        r.strategy = "noop"  # type: ignore[misc]

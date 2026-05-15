"""B4 wave 1 — `CriticAgent` parser + bootstrap 装载 + mock 跑通端到端。"""

from __future__ import annotations

from collections.abc import AsyncIterator

from chariot.agent.auxiliary_client import AuxiliaryClient
from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest
from chariot.agent.reflection import CriticAgent, CriticVerdict
from chariot.models.auxiliary import AuxiliaryClientEntry
from chariot.providers.base import BaseProvider, BaseProviderConfig
from chariot.providers.builtin.mock import MockProvider

# ---- _parse_verdict 单元测试 ----


def test_parse_verdict_pass() -> None:
    raw = "VERDICT: PASS\n\n产出符合任务要求,代码逻辑正确。"
    v = CriticAgent._parse_verdict(raw)
    assert v.verdict == "PASS"
    assert "产出符合" in v.reason


def test_parse_verdict_fail_with_explanation() -> None:
    raw = "VERDICT: FAIL\n\n排序函数没有实现:return x 直接返回入参。"
    v = CriticAgent._parse_verdict(raw)
    assert v.verdict == "FAIL"
    assert "排序函数" in v.reason


def test_parse_verdict_unsure() -> None:
    raw = "VERDICT: UNSURE\n\n看不到测试用例,无法判定正确性。"
    v = CriticAgent._parse_verdict(raw)
    assert v.verdict == "UNSURE"


def test_parse_verdict_tolerates_extra_whitespace() -> None:
    raw = "  VERDICT:   PASS  \n  good "
    v = CriticAgent._parse_verdict(raw)
    assert v.verdict == "PASS"


def test_parse_verdict_no_verdict_line_falls_back_to_unsure() -> None:
    raw = "这个看起来不错,但我不确定。"
    v = CriticAgent._parse_verdict(raw)
    assert v.verdict == "UNSURE"
    assert "不符合 VERDICT 契约" in v.reason
    assert v.raw == raw


def test_parse_verdict_empty_input() -> None:
    v = CriticAgent._parse_verdict("")
    assert v.verdict == "UNSURE"
    assert "为空" in v.reason


def test_parse_verdict_lowercase_rejected() -> None:
    """contract 要求 PASS|FAIL|UNSURE 大写;小写当格式错误。"""
    raw = "verdict: pass\nok"
    v = CriticAgent._parse_verdict(raw)
    assert v.verdict == "UNSURE"


def test_parse_verdict_no_reason_has_placeholder() -> None:
    raw = "VERDICT: PASS"
    v = CriticAgent._parse_verdict(raw)
    assert v.verdict == "PASS"
    assert v.reason == "(no reason provided)"


# ---- critique() 走 MockProvider 端到端 ----


class _StubVerdictProvider(BaseProvider):
    """yield 固定 VERDICT 输出的 stub。"""

    capabilities = MockProvider.capabilities

    def __init__(self, text: str) -> None:
        self.config = BaseProviderConfig(name="critic_stub", model="critic-1")
        self._text = text

    @classmethod
    def create(cls, options: dict[str, object]) -> _StubVerdictProvider:
        return cls("VERDICT: PASS\nok")

    async def generate(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        yield ChatEvent.message_start(
            message_id="msg_x", model="critic-1", usage={"input_tokens": 0, "output_tokens": 0}
        )
        yield ChatEvent.text_block_start(index=0)
        yield ChatEvent.text_delta(self._text, index=0)
        yield ChatEvent.block_stop(index=0)
        yield ChatEvent.message_delta_done(stop_reason="end_turn", usage={"output_tokens": 0})
        yield ChatEvent.message_done()


async def test_critique_end_to_end_with_stub_pass() -> None:
    aux = AuxiliaryClient(
        entry=AuxiliaryClientEntry.from_provider_id(name="critic", provider_id="critic_stub"),
        provider=_StubVerdictProvider("VERDICT: PASS\n\nlooks good"),
    )
    critic = CriticAgent(aux)
    v = await critic.critique(task_goal="sort()", produced="def sort(x): return sorted(x)")
    assert v.verdict == "PASS"
    assert "looks good" in v.reason


async def test_critique_end_to_end_with_stub_fail() -> None:
    aux = AuxiliaryClient(
        entry=AuxiliaryClientEntry.from_provider_id(name="critic", provider_id="critic_stub"),
        provider=_StubVerdictProvider("VERDICT: FAIL\n\nmissing impl"),
    )
    critic = CriticAgent(aux)
    v = await critic.critique(task_goal="sort()", produced="def sort(x): return x")
    assert v.verdict == "FAIL"
    assert "missing" in v.reason


async def test_critique_provider_error_returns_unsure() -> None:
    class _ErrProvider(BaseProvider):
        capabilities = MockProvider.capabilities

        def __init__(self) -> None:
            self.config = BaseProviderConfig(name="err", model="x")

        @classmethod
        def create(cls, options: dict[str, object]) -> _ErrProvider:
            return cls()

        async def generate(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
            yield ChatEvent.error_event(error_type="upstream_stream_error", error_message="boom")

    aux = AuxiliaryClient(
        entry=AuxiliaryClientEntry.from_provider_id(name="critic", provider_id="err"),
        provider=_ErrProvider(),
    )
    critic = CriticAgent(aux)
    v = await critic.critique(task_goal="x", produced="y")
    assert v.verdict == "UNSURE"
    assert "上游错误" in v.reason


async def test_critique_with_mock_provider_falls_back_to_unsure() -> None:
    """MockProvider echo 输入文本,不含 VERDICT 行 → parser 兜底 UNSURE。
    顺便验证 critic + mock 这条 sanity 链路。"""
    aux = AuxiliaryClient(
        entry=AuxiliaryClientEntry.from_provider_id(name="critic", provider_id="mock"),
        provider=MockProvider.create({}),
    )
    critic = CriticAgent(aux)
    v = await critic.critique(task_goal="sort", produced="x")
    assert v.verdict == "UNSURE"
    assert "不符合 VERDICT 契约" in v.reason


# ---- from_auxiliary_clients ----


def test_from_auxiliary_clients_finds_critic() -> None:
    entries = [
        AuxiliaryClientEntry.from_provider_id(name="summarizer", provider_id="mock"),
        AuxiliaryClientEntry.from_provider_id(name="critic", provider_id="mock", model="mock-1"),
    ]
    providers = {"mock": MockProvider.create({})}
    critic = CriticAgent.from_auxiliary_clients(entries, providers)
    assert critic is not None
    assert critic.aux.entry.name == "critic"


def test_from_auxiliary_clients_returns_none_without_critic_row() -> None:
    entries = [AuxiliaryClientEntry.from_provider_id(name="summarizer", provider_id="mock")]
    providers = {"mock": MockProvider.create({})}
    critic = CriticAgent.from_auxiliary_clients(entries, providers)
    assert critic is None


def test_from_auxiliary_clients_returns_none_when_provider_dangling() -> None:
    entries = [AuxiliaryClientEntry.from_provider_id(name="critic", provider_id="ghost")]
    providers = {"mock": MockProvider.create({})}
    critic = CriticAgent.from_auxiliary_clients(entries, providers)
    assert critic is None


# ---- CriticVerdict 是 frozen dataclass ----


def test_critic_verdict_frozen() -> None:
    import pytest

    v = CriticVerdict(verdict="PASS", reason="x", raw="y")
    with pytest.raises(AttributeError):
        v.verdict = "FAIL"  # type: ignore[misc]

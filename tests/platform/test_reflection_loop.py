"""B4 wave 2 — `ReflectionLoop` 触发 / 重试预算 / circuit breaker / record 累积。"""

from __future__ import annotations

import pytest

from chariot.agent.auxiliary_client import AuxiliaryClient
from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest, Message
from chariot.agent.reflection import (
    AssistantBuffer,
    CriticAgent,
    CriticVerdict,
    ReflectionLoop,
)
from chariot.agent.reflection.critic import _VERDICT_LINE_PATTERN  # noqa: F401 — sanity import
from chariot.models.auxiliary import AuxiliaryClientEntry


class _StubCritic(CriticAgent):
    """跳过 BaseProvider,直接返预设 verdict 序列。"""

    def __init__(self, verdicts: list[CriticVerdict]) -> None:
        # 不调 super().__init__ — 不需要真 aux
        self._verdicts = list(verdicts)
        self._aux = None  # type: ignore[assignment]
        self.called_with: list[tuple[str, str, str | None]] = []

    async def critique(self, *, task_goal: str, produced: str, extra_context: str | None = None) -> CriticVerdict:
        self.called_with.append((task_goal, produced, extra_context))
        if not self._verdicts:
            return CriticVerdict(verdict="PASS", reason="default stub PASS", raw="")
        return self._verdicts.pop(0)


def _v(verdict: str, reason: str = "stub reason") -> CriticVerdict:
    return CriticVerdict(verdict=verdict, reason=reason, raw=f"VERDICT: {verdict}\n{reason}")  # type: ignore[arg-type]


def _req(text: str = "do the task") -> ChatRequest:
    return ChatRequest(provider_ref="mock", messages=[Message(role="user", content=text)])


# ---- AssistantBuffer ----


def test_buffer_collects_text_delta() -> None:
    buf = AssistantBuffer()
    buf.accept(ChatEvent.text_delta("hello ", index=0))
    buf.accept(ChatEvent.text_delta("world", index=0))
    assert buf.assistant_text == "hello world"


def test_buffer_marks_tool_failures() -> None:
    buf = AssistantBuffer()
    buf.accept(ChatEvent.tool_result_event(tool_use_id="t1", content="boom", is_error=True))
    buf.accept(ChatEvent.tool_result_event(tool_use_id="t2", content="ok"))
    assert buf.tool_failures == 1


def test_buffer_marks_error_event() -> None:
    buf = AssistantBuffer()
    buf.accept(ChatEvent.error_event(error_type="upstream", error_message="x"))
    assert buf.saw_error_event is True
    assert buf.error_type == "upstream"


def test_buffer_detects_self_report_fail() -> None:
    buf = AssistantBuffer()
    buf.accept(ChatEvent.text_delta("FAILED: 排序函数没实现", index=0))
    assert buf.has_self_report_fail() is True


def test_buffer_no_self_report_for_normal_text() -> None:
    buf = AssistantBuffer()
    buf.accept(ChatEvent.text_delta("已完成,代码如下", index=0))
    assert buf.has_self_report_fail() is False


# ---- detect_trigger 优先级 ----


def test_detect_trigger_returns_none_for_clean_output() -> None:
    buf = AssistantBuffer()
    buf.accept(ChatEvent.text_delta("ok", index=0))
    assert ReflectionLoop.detect_trigger(buf) is None


def test_detect_trigger_error_event_wins() -> None:
    buf = AssistantBuffer()
    buf.accept(ChatEvent.text_delta("FAILED: x", index=0))
    buf.accept(ChatEvent.error_event(error_type="boom", error_message="x"))
    assert ReflectionLoop.detect_trigger(buf) == "error_event"


def test_detect_trigger_tool_failure_over_self_report() -> None:
    buf = AssistantBuffer()
    buf.accept(ChatEvent.text_delta("FAILED: x", index=0))
    buf.accept(ChatEvent.tool_result_event(tool_use_id="t1", content="boom", is_error=True))
    assert ReflectionLoop.detect_trigger(buf) == "tool_failure"


def test_detect_trigger_self_report_when_only_signal() -> None:
    buf = AssistantBuffer()
    buf.accept(ChatEvent.text_delta("FAILED: 排序没实现", index=0))
    assert ReflectionLoop.detect_trigger(buf) == "self_report_fail"


# ---- ReflectionLoop.step ----


async def test_step_returns_none_when_no_trigger() -> None:
    critic = _StubCritic([])
    loop = ReflectionLoop(critic=critic, max_retries=2)
    buf = AssistantBuffer()
    buf.accept(ChatEvent.text_delta("clean output", index=0))
    result = await loop.step(buffer=buf, original_req=_req())
    assert result is None
    assert not critic.called_with  # critic 不该被调


async def test_step_pass_stops_with_record() -> None:
    critic = _StubCritic([_v("PASS", "all good")])
    loop = ReflectionLoop(critic=critic, max_retries=2)
    buf = AssistantBuffer()
    buf.accept(ChatEvent.tool_result_event(tool_use_id="t1", content="boom", is_error=True))
    result = await loop.step(buffer=buf, original_req=_req())
    assert result is not None
    assert result.should_retry is False
    assert result.record.verdict == "PASS"
    assert result.record.retry_decision == "pass_stop"
    assert loop.records[-1].iteration == 1


async def test_step_fail_triggers_retry_with_revised_req() -> None:
    critic = _StubCritic([_v("FAIL", "缺 X")])
    loop = ReflectionLoop(critic=critic, max_retries=2)
    buf = AssistantBuffer()
    buf.accept(ChatEvent.tool_result_event(tool_use_id="t1", content="boom", is_error=True))
    result = await loop.step(buffer=buf, original_req=_req("write sort"))
    assert result is not None
    assert result.should_retry is True
    assert result.revised_req is not None
    # 末尾消息是 [REFLECTION] 注入
    last = result.revised_req.messages[-1]
    assert last.role == "user"
    assert isinstance(last.content, str)
    assert "[REFLECTION]" in last.content
    assert "FAIL" in last.content
    assert "缺 X" in last.content


async def test_step_unsure_does_not_consume_retry() -> None:
    critic = _StubCritic([_v("UNSURE", "no test cases")])
    loop = ReflectionLoop(critic=critic, max_retries=2)
    buf = AssistantBuffer()
    buf.accept(ChatEvent.tool_result_event(tool_use_id="t1", content="boom", is_error=True))
    result = await loop.step(buffer=buf, original_req=_req())
    assert result is not None
    assert result.should_retry is False
    assert result.record.retry_decision == "verdict_unsure_stop"


async def test_step_budget_exhausted_after_max_retries() -> None:
    critic = _StubCritic([_v("FAIL"), _v("FAIL"), _v("FAIL")])
    loop = ReflectionLoop(critic=critic, max_retries=2)
    # iteration 1: FAIL → retry
    buf1 = AssistantBuffer()
    buf1.accept(ChatEvent.tool_result_event(tool_use_id="t1", content="boom", is_error=True))
    r1 = await loop.step(buffer=buf1, original_req=_req())
    assert r1 is not None and r1.should_retry is True

    # iteration 2: FAIL → retry
    buf2 = AssistantBuffer()
    buf2.accept(ChatEvent.tool_result_event(tool_use_id="t1", content="boom", is_error=True))
    r2 = await loop.step(buffer=buf2, original_req=r1.revised_req)  # type: ignore[arg-type]
    assert r2 is not None and r2.should_retry is True

    # iteration 3: 超 max_retries=2 → budget_exhausted
    buf3 = AssistantBuffer()
    buf3.accept(ChatEvent.tool_result_event(tool_use_id="t1", content="boom", is_error=True))
    r3 = await loop.step(buffer=buf3, original_req=r2.revised_req)  # type: ignore[arg-type]
    assert r3 is not None and r3.should_retry is False
    assert r3.record.retry_decision == "budget_exhausted"


async def test_step_circuit_breaker_after_consecutive_fails() -> None:
    """3 consecutive fails (default threshold) → circuit_open。"""
    critic = _StubCritic([_v("FAIL"), _v("FAIL"), _v("FAIL"), _v("FAIL")])
    loop = ReflectionLoop(critic=critic, max_retries=10, circuit_breaker_consecutive_fail=3)
    current = _req()
    for _ in range(3):
        buf = AssistantBuffer()
        buf.accept(ChatEvent.tool_result_event(tool_use_id="t1", content="boom", is_error=True))
        r = await loop.step(buffer=buf, original_req=current)
        assert r is not None
        if r.should_retry:
            current = r.revised_req  # type: ignore[assignment]
    # 第 4 次 step → consecutive_fails=3 命中 circuit_open
    buf4 = AssistantBuffer()
    buf4.accept(ChatEvent.tool_result_event(tool_use_id="t1", content="boom", is_error=True))
    r4 = await loop.step(buffer=buf4, original_req=current)
    assert r4 is not None
    assert r4.record.retry_decision == "circuit_open"


async def test_step_pass_resets_consecutive_fails() -> None:
    """PASS 重置 consecutive_fails 计数(虽然 PASS 后我们不会再 step,但语义上确认)。"""
    critic = _StubCritic([_v("FAIL"), _v("PASS")])
    loop = ReflectionLoop(critic=critic, max_retries=5)
    buf1 = AssistantBuffer()
    buf1.accept(ChatEvent.tool_result_event(tool_use_id="t1", content="boom", is_error=True))
    await loop.step(buffer=buf1, original_req=_req())
    buf2 = AssistantBuffer()
    buf2.accept(ChatEvent.tool_result_event(tool_use_id="t1", content="boom", is_error=True))
    await loop.step(buffer=buf2, original_req=_req())
    # consecutive_fails 在 PASS 后归 0
    assert loop._consecutive_fails == 0


# ---- 构造校验 ----


def test_loop_rejects_negative_max_retries() -> None:
    with pytest.raises(ValueError):
        ReflectionLoop(critic=_StubCritic([]), max_retries=-1)


def test_loop_rejects_zero_circuit_breaker() -> None:
    with pytest.raises(ValueError):
        ReflectionLoop(critic=_StubCritic([]), circuit_breaker_consecutive_fail=0)


# ---- critique 上下文传递 ----


async def test_step_passes_tool_failure_count_in_extra_context() -> None:
    critic = _StubCritic([_v("FAIL")])
    loop = ReflectionLoop(critic=critic, max_retries=2)
    buf = AssistantBuffer()
    buf.accept(ChatEvent.tool_result_event(tool_use_id="t1", content="boom", is_error=True))
    buf.accept(ChatEvent.tool_result_event(tool_use_id="t2", content="boom2", is_error=True))
    await loop.step(buffer=buf, original_req=_req())
    # critic 收到 extra_context 提到 2 个工具失败
    _, _, extra = critic.called_with[0]
    assert extra is not None
    assert "2" in extra


# ---- 集成 from_auxiliary_clients 走真 critic + mock provider(sanity) ----


async def test_step_with_real_critic_falls_back_unsure() -> None:
    """真 CriticAgent(MockProvider 不会输出 VERDICT)→ UNSURE → 不重试。"""
    aux = AuxiliaryClient(
        entry=AuxiliaryClientEntry.from_provider_id(name="critic", provider_id="mock"),
        provider=__import__("chariot.providers.builtin.mock", fromlist=["MockProvider"]).MockProvider.create({}),
    )
    critic = CriticAgent(aux)
    loop = ReflectionLoop(critic=critic, max_retries=2)
    buf = AssistantBuffer()
    buf.accept(ChatEvent.tool_result_event(tool_use_id="t1", content="boom", is_error=True))
    result = await loop.step(buffer=buf, original_req=_req())
    assert result is not None
    assert result.record.verdict == "UNSURE"
    assert result.should_retry is False

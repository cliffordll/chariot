"""B4 wave 2 — AIAgent.run_chat 反思路径端到端。

构造场景:agent 装一个 stub critic(预设 FAIL → PASS),strategically inject
tool_failure 触发反思 → 第一轮 FAIL → 第二轮 PASS;trace_turns 出现两条记录,
第二条 meta 含 reflection_iteration=1 + previous_verdict='FAIL'。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from sqlalchemy import select

from chariot.agent.auxiliary_client import AuxiliaryClient
from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest, Message
from chariot.agent.reflection import CriticAgent, CriticVerdict
from chariot.agent.registry import AgentRegistry
from chariot.agent.run import AIAgent
from chariot.database.models import TraceTurnRow
from chariot.database.session import dispose_db
from chariot.models.auxiliary import AuxiliaryClientEntry
from chariot.providers.base import BaseProvider, BaseProviderConfig
from chariot.providers.builtin.mock import MockProvider


class _ToolFailFirstThenSuccessProvider(BaseProvider):
    """两阶段 provider:首次调用 yield tool_use 一次(让 AgentLoop 跑工具,工具失败);
    第二次调用 yield 干净文本(任务成功)。
    实际上我们简化:直接 yield 一个 is_error 的 tool_result 事件不可行(那是 AgentLoop 产);
    所以这里 yield content_block 文本 'all good' 第二次,第一次 yield content_block 含 'FAILED:' 字串。
    """

    capabilities = MockProvider.capabilities

    def __init__(self) -> None:
        self.config = BaseProviderConfig(name="mock", model="mock-1")
        self._call_count = 0

    @classmethod
    def create(cls, options: dict[str, object]) -> _ToolFailFirstThenSuccessProvider:
        return cls()

    async def generate(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        self._call_count += 1
        text = "FAILED: 排序没实现" if self._call_count == 1 else "已正确实现 sorted()"
        yield ChatEvent.message_start(
            message_id=f"msg_{self._call_count}",
            model="mock-1",
            usage={"input_tokens": 0, "output_tokens": 0},
        )
        yield ChatEvent.text_block_start(index=0)
        yield ChatEvent.text_delta(text, index=0)
        yield ChatEvent.block_stop(index=0)
        yield ChatEvent.message_delta_done(stop_reason="end_turn", usage={"output_tokens": 0})
        yield ChatEvent.message_done()


class _StubCritic(CriticAgent):
    """直接返预设 verdict 序列,不调底层 provider。"""

    def __init__(self, verdicts: list[CriticVerdict]) -> None:
        self._verdicts = list(verdicts)
        self._aux = None  # type: ignore[assignment]

    async def critique(self, *, task_goal: str, produced: str, extra_context: str | None = None) -> CriticVerdict:
        if not self._verdicts:
            return CriticVerdict(verdict="PASS", reason="exhausted stub", raw="")
        return self._verdicts.pop(0)


@pytest_asyncio.fixture
async def agent(tmp_path: Path):
    a = await AIAgent.bootstrap(tmp_path / "test.db")
    # 注 stub provider 替代 mock(让 self-report FAILED 触发反思)
    a._providers["mock"] = _ToolFailFirstThenSuccessProvider()
    # 注 stub critic:第一次 FAIL → 反思,第二次... 不会被调(第二轮 produced='已正确实现' 不触发 self-report fail)
    a._critic_agent = _StubCritic([CriticVerdict(verdict="FAIL", reason="缺实现", raw="VERDICT: FAIL\n缺实现")])
    AgentRegistry._agents.clear()
    try:
        yield a
    finally:
        AgentRegistry._agents.clear()
        await dispose_db()


async def test_reflection_disabled_runs_single_turn(agent: AIAgent) -> None:
    """req.reflection_enabled=False → 单轮,即使产出含 FAILED 也不重试。"""
    req = ChatRequest(
        provider_name="mock",
        messages=[Message(role="user", content="write sort")],
        reflection_enabled=False,
    )
    events = [ev async for ev in agent.run_chat(req)]
    assert any(ev.kind == "stream_done" for ev in events)
    # 只有一次 stream_done(单轮)
    assert sum(1 for ev in events if ev.kind == "stream_done") == 1
    # 内容是 FAILED 那条(provider call 只被调一次)
    text_chunks = [ev.delta["text"] for ev in events if ev.kind == "content_block_delta" and ev.delta]
    assert any("FAILED" in t for t in text_chunks)
    # trace_turns 只有 1 条
    async with agent.session_maker() as session:
        rows = (await session.execute(select(TraceTurnRow))).scalars().all()
    assert len(rows) == 1
    assert json.loads(rows[0].meta) == {}


async def test_reflection_enabled_triggers_retry_on_self_report_fail(agent: AIAgent) -> None:
    """req.reflection_enabled=True + FAILED 自报 → critic FAIL → 第二轮跑成功。

    验证:
    - 两次 stream_done(两轮)
    - trace_turns 两条;第二条 meta 含 reflection_iteration=1 + previous_verdict='FAIL'
    """
    req = ChatRequest(
        provider_name="mock",
        messages=[Message(role="user", content="write sort")],
        reflection_enabled=True,
        reflection_max_retries=2,
    )
    events = [ev async for ev in agent.run_chat(req)]
    assert sum(1 for ev in events if ev.kind == "stream_done") == 2

    # provider 被调了两次(_call_count 验证)
    provider = agent.providers["mock"]
    assert isinstance(provider, _ToolFailFirstThenSuccessProvider)
    # providers() 属性返 copy,直接走 _providers
    assert agent._providers["mock"]._call_count == 2

    # trace_turns 两条
    async with agent.session_maker() as session:
        rows = (await session.execute(select(TraceTurnRow).order_by(TraceTurnRow.started_at.asc()))).scalars().all()
    assert len(rows) == 2
    first_meta = json.loads(rows[0].meta)
    second_meta = json.loads(rows[1].meta)
    assert first_meta == {}  # 首轮无反思 meta
    assert second_meta["reflection_iteration"] == 1
    assert second_meta["reflection_trigger"] == "self_report_fail"
    assert second_meta["reflection_previous_verdict"] == "FAIL"
    assert "缺实现" in second_meta["reflection_previous_reason"]


async def test_reflection_enabled_no_critic_falls_back_to_single(tmp_path: Path) -> None:
    """critic 未装载 → req.reflection_enabled=True 也走单轮(不触发反思)。"""
    a = await AIAgent.bootstrap(tmp_path / "test.db")
    a._providers["mock"] = _ToolFailFirstThenSuccessProvider()
    # 故意不装 critic
    a._critic_agent = None
    AgentRegistry._agents.clear()
    try:
        req = ChatRequest(
            provider_name="mock",
            messages=[Message(role="user", content="x")],
            reflection_enabled=True,
            reflection_max_retries=2,
        )
        events = [ev async for ev in a.run_chat(req)]
    finally:
        AgentRegistry._agents.clear()
        await dispose_db()
    assert sum(1 for ev in events if ev.kind == "stream_done") == 1


async def test_reflection_max_retries_zero_disables(tmp_path: Path) -> None:
    """reflection_max_retries=0 → 即使 reflection_enabled=True 也单轮。"""
    a = await AIAgent.bootstrap(tmp_path / "test.db")
    a._providers["mock"] = _ToolFailFirstThenSuccessProvider()
    a._critic_agent = _StubCritic([CriticVerdict(verdict="FAIL", reason="x", raw="")])
    AgentRegistry._agents.clear()
    try:
        req = ChatRequest(
            provider_name="mock",
            messages=[Message(role="user", content="x")],
            reflection_enabled=True,
            reflection_max_retries=0,
        )
        events = [ev async for ev in a.run_chat(req)]
    finally:
        AgentRegistry._agents.clear()
        await dispose_db()
    assert sum(1 for ev in events if ev.kind == "stream_done") == 1


# stub helper: AuxiliaryClient 直接构造(无需真 provider)用在导入校验上
def _build_aux_for_imports() -> AuxiliaryClient:
    return AuxiliaryClient(
        entry=AuxiliaryClientEntry(name="critic", provider_entry="mock"),
        provider=MockProvider.create({}),
    )


def test_build_aux_for_imports_smoke() -> None:
    aux = _build_aux_for_imports()
    assert aux.name == "critic"

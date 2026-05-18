"""B4 wave 3 — agent_profiles.reflection_* + AIAgent._apply_profile_reflection。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.chat_request import ChatRequest, Message
from chariot.agent.reflection import CriticAgent, CriticVerdict
from chariot.agent.registry import AgentRegistry
from chariot.agent.run import AIAgent
from chariot.database.session import dispose_db, init_db
from chariot.repos.task_repo import TaskRepo
from chariot.services.agent import AgentService


@pytest_asyncio.fixture
async def session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    sm = await init_db(tmp_path / "test.db")
    async with sm() as s:
        yield s
    await dispose_db()


# ---- TaskRepo CRUD reflection 字段 ----


async def test_create_agent_profile_defaults_reflection_off(session: AsyncSession) -> None:
    repo = TaskRepo(session)
    entry = await repo.create_agent_profile(name="a1", role="dev")
    assert entry.reflection_enabled is False
    assert entry.reflection_max_retries == 2


async def test_create_agent_profile_with_reflection_on(session: AsyncSession) -> None:
    repo = TaskRepo(session)
    entry = await repo.create_agent_profile(
        name="a1",
        role="dev",
        reflection_enabled=True,
        reflection_max_retries=5,
    )
    assert entry.reflection_enabled is True
    assert entry.reflection_max_retries == 5


async def test_update_agent_profile_toggles_reflection(session: AsyncSession) -> None:
    repo = TaskRepo(session)
    await repo.create_agent_profile(name="a1", role="dev")
    entry = await repo.update_agent_profile(name="a1", reflection_enabled=True)
    assert entry.reflection_enabled is True
    entry = await repo.update_agent_profile(name="a1", reflection_enabled=False)
    assert entry.reflection_enabled is False


async def test_update_agent_profile_changes_retries(session: AsyncSession) -> None:
    repo = TaskRepo(session)
    await repo.create_agent_profile(name="a1", role="dev")
    entry = await repo.update_agent_profile(name="a1", reflection_max_retries=7)
    assert entry.reflection_max_retries == 7


async def test_update_agent_profile_rejects_negative_retries(session: AsyncSession) -> None:
    from chariot.agent.exceptions import ConfigError

    repo = TaskRepo(session)
    await repo.create_agent_profile(name="a1", role="dev")
    with pytest.raises(ConfigError):
        await repo.update_agent_profile(name="a1", reflection_max_retries=-1)


# ---- AIAgent._apply_profile_reflection ----


class _StubCritic(CriticAgent):
    def __init__(self) -> None:
        self._aux = None  # type: ignore[assignment]

    async def critique(self, *, task_goal: str, produced: str, extra_context: str | None = None) -> CriticVerdict:
        return CriticVerdict(verdict="PASS", reason="ok", raw="")


@pytest_asyncio.fixture
async def agent(tmp_path: Path) -> AsyncIterator[AIAgent]:
    a = await AIAgent.bootstrap(tmp_path / "test.db")
    a._critic_agent = _StubCritic()
    AgentRegistry._agents.clear()
    try:
        yield a
    finally:
        AgentRegistry._agents.clear()
        await dispose_db()


async def test_apply_profile_reflection_no_agent_profile_keeps_req(agent: AIAgent) -> None:
    req = ChatRequest(provider_ref="mock", messages=[Message(role="user", content="x")])
    out = await agent._apply_profile_reflection(req)
    assert out.reflection_enabled is False  # no profile, no change


async def test_apply_profile_reflection_with_enabled_profile(agent: AIAgent) -> None:
    await AgentService(agent).create_agent(
        name="alpha",
        role="dev",
        reflection_enabled=True,
        reflection_max_retries=5,
    )
    req = ChatRequest(
        provider_ref="mock",
        messages=[Message(role="user", content="x")],
        agent_profile="alpha",
    )
    out = await agent._apply_profile_reflection(req)
    assert out.reflection_enabled is True
    assert out.reflection_max_retries == 5


async def test_apply_profile_reflection_disabled_profile_no_op(agent: AIAgent) -> None:
    await AgentService(agent).create_agent(
        name="beta",
        role="dev",
        reflection_enabled=False,
    )
    req = ChatRequest(
        provider_ref="mock",
        messages=[Message(role="user", content="x")],
        agent_profile="beta",
    )
    out = await agent._apply_profile_reflection(req)
    assert out.reflection_enabled is False


async def test_apply_profile_reflection_req_explicit_overrides_profile(agent: AIAgent) -> None:
    """req.reflection_enabled=True 已显式 → 不被 profile 覆盖(尊重显式 flag)。"""
    await AgentService(agent).create_agent(
        name="gamma",
        role="dev",
        reflection_enabled=False,  # profile 关
    )
    req = ChatRequest(
        provider_ref="mock",
        messages=[Message(role="user", content="x")],
        agent_profile="gamma",
        reflection_enabled=True,  # req 显式开
        reflection_max_retries=9,
    )
    out = await agent._apply_profile_reflection(req)
    assert out.reflection_enabled is True
    assert out.reflection_max_retries == 9  # 用 req 的,不被 profile 覆盖

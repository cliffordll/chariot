"""B6 wave 2 —— `agent_profile.default_skill` 字段 + AIAgent._maybe_activate_skill 透传。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.chat_request import ChatRequest, Message
from chariot.agent.registry import AgentRegistry
from chariot.agent.run import _NO_BINDING, AIAgent, _AgentBinding
from chariot.database.session import dispose_db, init_db
from chariot.models.agent import UNSET
from chariot.repos.task_repo import TaskRepo


@pytest_asyncio.fixture
async def session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    sm = await init_db(tmp_path / "test.db")
    async with sm() as s:
        yield s
    await dispose_db()


# ---- AgentProfile CRUD default_skill ----


async def test_create_agent_profile_default_skill_none(session: AsyncSession) -> None:
    repo = TaskRepo(session)
    entry = await repo.create_agent_profile(name="a1", role="dev")
    assert entry.default_skill is None


async def test_create_agent_profile_with_default_skill(session: AsyncSession) -> None:
    repo = TaskRepo(session)
    entry = await repo.create_agent_profile(name="a1", role="dev", default_skill="code_review")
    assert entry.default_skill == "code_review"


async def test_update_agent_profile_sets_default_skill(session: AsyncSession) -> None:
    repo = TaskRepo(session)
    await repo.create_agent_profile(name="a1", role="dev")
    entry = await repo.update_agent_profile(name="a1", default_skill="debug_helper")
    assert entry.default_skill == "debug_helper"


async def test_update_agent_profile_clears_default_skill_with_none(session: AsyncSession) -> None:
    """ClearableStr 语义:显式传 None 清空字段。"""
    repo = TaskRepo(session)
    await repo.create_agent_profile(name="a1", role="dev", default_skill="x")
    entry = await repo.update_agent_profile(name="a1", default_skill=None)
    assert entry.default_skill is None


async def test_update_agent_profile_unset_keeps_default_skill(session: AsyncSession) -> None:
    """ClearableStr 语义:UNSET → 不动字段。"""
    repo = TaskRepo(session)
    await repo.create_agent_profile(name="a1", role="dev", default_skill="x")
    # default_skill 不传(默认 UNSET)→ 保持
    entry = await repo.update_agent_profile(name="a1", default_skill=UNSET)
    assert entry.default_skill == "x"


# ---- AIAgent._maybe_activate_skill 透传 ----


async def test_skill_activated_from_request_field(tmp_path: Path) -> None:
    agent = await AIAgent.bootstrap(tmp_path / "skill_act.db")
    AgentRegistry._agents.clear()
    try:
        req = ChatRequest(
            provider_name="mock",
            messages=[Message(role="user", content="hi")],
            skill="code_review",
            tools=[],
        )
        out = await agent._maybe_activate_skill(req, _NO_BINDING)
        assert isinstance(out.system, str)
        assert "code_review" in out.system or "<skill" in out.system
    finally:
        AgentRegistry._agents.clear()
        await dispose_db()


async def test_skill_activated_from_profile_default_when_request_none(tmp_path: Path) -> None:
    agent = await AIAgent.bootstrap(tmp_path / "skill_act.db")
    AgentRegistry._agents.clear()
    try:
        from chariot.models.agent import AgentProfile

        profile = AgentProfile(name="x", role="dev", default_skill="debug_helper")
        binding = _AgentBinding(profile=profile)
        req = ChatRequest(
            provider_name="mock",
            messages=[Message(role="user", content="hi")],
            tools=[],
        )
        out = await agent._maybe_activate_skill(req, binding)
        assert isinstance(out.system, str)
        assert "debug_helper" in out.system
    finally:
        AgentRegistry._agents.clear()
        await dispose_db()


async def test_request_empty_string_overrides_profile_default(tmp_path: Path) -> None:
    """req.skill == "" → 显式清空,profile.default_skill 不起作用。"""
    agent = await AIAgent.bootstrap(tmp_path / "skill_act.db")
    AgentRegistry._agents.clear()
    try:
        from chariot.models.agent import AgentProfile

        profile = AgentProfile(name="x", role="dev", default_skill="debug_helper")
        binding = _AgentBinding(profile=profile)
        req = ChatRequest(
            provider_name="mock",
            messages=[Message(role="user", content="hi")],
            skill="",
            tools=[],
        )
        out = await agent._maybe_activate_skill(req, binding)
        # system 没被注入 <skill> 块
        assert out.system is None or "<skill" not in (out.system if isinstance(out.system, str) else "")
    finally:
        AgentRegistry._agents.clear()
        await dispose_db()


async def test_dangling_skill_name_silent_fallback(tmp_path: Path) -> None:
    """req.skill 指向不存在的 skill → activator 不抛,req 原样返回。"""
    agent = await AIAgent.bootstrap(tmp_path / "skill_act.db")
    AgentRegistry._agents.clear()
    try:
        req = ChatRequest(
            provider_name="mock",
            messages=[Message(role="user", content="hi")],
            skill="does_not_exist",
            tools=[],
        )
        out = await agent._maybe_activate_skill(req, _NO_BINDING)
        assert out.system is None
    finally:
        AgentRegistry._agents.clear()
        await dispose_db()

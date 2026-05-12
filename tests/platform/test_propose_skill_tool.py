"""B6 wave 3 —— `ProposeSkillTool` schema + execute + service 注入 + guardrail 集成。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chariot.agent.config import Capabilities
from chariot.audit.hooks import AuditHookManager
from chariot.checkpoints import CheckpointManager
from chariot.database.session import dispose_db, init_db
from chariot.guardrails import GuardrailEngine, Verdict
from chariot.guardrails.approval import ApprovalPolicy
from chariot.models.tool import ToolEntry
from chariot.repos.skill_repo import SkillRepo
from chariot.skills.propose_service import SkillProposeService
from chariot.tools.builtin.propose_skill import ProposeSkillTool
from chariot.tools.execution import ToolExecutionService


@pytest_asyncio.fixture
async def sm_path(tmp_path: Path) -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], Path]]:
    db_path = tmp_path / "chariot.db"
    sm = await init_db(db_path)
    try:
        yield sm, db_path
    finally:
        await dispose_db()


def _make_tool() -> ProposeSkillTool:
    return ProposeSkillTool.create(ToolEntry(name="propose_skill", type="propose_skill", enabled=True, options={}))


def _good_input(name: str = "auto_w") -> dict[str, object]:
    return {
        "name": name,
        "description": "do thing well in this codebase",
        "prompt": "be careful when refactoring; explain your reasoning step by step.",
    }


# ---- schema ----


def test_schema_required_fields() -> None:
    tool = _make_tool()
    s = tool.schema()
    assert s["name"] == "propose_skill"
    props = s["input_schema"]["properties"]
    assert "name" in props
    assert "description" in props
    assert "prompt" in props
    assert s["input_schema"]["required"] == ["name", "description", "prompt"]
    # additionalProperties=False:agent 不能塞奇怪字段
    assert s["input_schema"]["additionalProperties"] is False


# ---- service 未注入 ----


async def test_execute_without_service_returns_error() -> None:
    tool = _make_tool()
    out = await tool.execute(_good_input())
    assert out["is_error"] is True
    assert "service" in out["content"][0]["text"]


# ---- execute 成功路径(attach_service 后) ----


async def test_execute_with_service_creates_skill(
    sm_path: tuple[async_sessionmaker[AsyncSession], Path],
    tmp_path: Path,
) -> None:
    sm, db_path = sm_path
    hooks = AuditHookManager(sm)
    mgr = CheckpointManager(
        sessionmaker=sm,
        db_path=db_path,
        checkpoint_dir=tmp_path / "ckpt",
        config_files=[tmp_path / "nx.yaml"],
        cwd=tmp_path / "not_git",
        audit_hooks=hooks,
    )
    svc = SkillProposeService(sessionmaker=sm, audit_hooks=hooks, checkpoint_manager=mgr)
    tool = _make_tool()
    tool.attach_service(svc)

    out = await tool.execute(_good_input("auto_w"))
    assert out.get("is_error", False) is False
    text = out["content"][0]["text"]
    assert "auto_w" in text
    assert "chariot skill enable auto_w" in text

    async with sm() as session:
        entry = await SkillRepo(session).get_by_name("auto_w")
        assert entry is not None
        assert entry.enabled is False


async def test_execute_missing_field_returns_error() -> None:
    tool = _make_tool()
    # service 不需要(校验先于 service)
    out = await tool.execute({"name": "x"})
    assert out["is_error"] is True


# ---- guardrail 集成(self_modify_chariot 拦截 propose_skill)----


async def test_guardrail_denies_without_capability(
    sm_path: tuple[async_sessionmaker[AsyncSession], Path],
    tmp_path: Path,
) -> None:
    """enable_self_mod=False → DENY,tool 都没机会跑;不写 skill row。"""
    sm, db_path = sm_path
    hooks = AuditHookManager(sm)
    capabilities = Capabilities(enable_self_mod=False, yolo=False)
    mgr = CheckpointManager(
        sessionmaker=sm,
        db_path=db_path,
        checkpoint_dir=tmp_path / "ckpt",
        config_files=[tmp_path / "nx.yaml"],
        cwd=tmp_path / "not_git",
        audit_hooks=hooks,
    )
    svc = SkillProposeService(sessionmaker=sm, audit_hooks=hooks, checkpoint_manager=mgr)
    tool = _make_tool()
    tool.attach_service(svc)

    engine = GuardrailEngine.with_defaults(capabilities=capabilities)
    approval = ApprovalPolicy(capabilities=capabilities)
    exec_svc = ToolExecutionService(
        {"propose_skill": tool},
        guardrail_engine=engine,
        approval_policy=approval,
        audit_hooks=hooks,
    )

    event = await exec_svc.execute_tool_call(
        tool_use_id="t1",
        tool_name="propose_skill",
        tool_input=_good_input("blocked_x"),
    )
    assert event.is_error is True
    # propose 没 row(被 guardrail 拦截了)
    async with sm() as session:
        entry = await SkillRepo(session).get_by_name("blocked_x")
        assert entry is None


async def test_guardrail_denies_when_capability_on_but_yolo_off(
    sm_path: tuple[async_sessionmaker[AsyncSession], Path],
    tmp_path: Path,
) -> None:
    """enable_self_mod=True + yolo=False → 引擎升级为 REQUIRE_APPROVAL,ApprovalPolicy
    没 yolo 也拒绝。tool 不应跑通。"""
    sm, db_path = sm_path
    hooks = AuditHookManager(sm)
    capabilities = Capabilities(enable_self_mod=True, yolo=False)
    mgr = CheckpointManager(
        sessionmaker=sm,
        db_path=db_path,
        checkpoint_dir=tmp_path / "ckpt",
        config_files=[tmp_path / "nx.yaml"],
        cwd=tmp_path / "not_git",
        audit_hooks=hooks,
    )
    svc = SkillProposeService(sessionmaker=sm, audit_hooks=hooks, checkpoint_manager=mgr)
    tool = _make_tool()
    tool.attach_service(svc)

    engine = GuardrailEngine.with_defaults(capabilities=capabilities)
    approval = ApprovalPolicy(capabilities=capabilities)
    # 确认 engine 把 DENY 升级到 REQUIRE_APPROVAL
    verdict = engine.preview(tool_name="propose_skill", args=_good_input("blocked_y"))
    assert verdict.verdict == Verdict.REQUIRE_APPROVAL

    exec_svc = ToolExecutionService(
        {"propose_skill": tool},
        guardrail_engine=engine,
        approval_policy=approval,
        audit_hooks=hooks,
    )
    event = await exec_svc.execute_tool_call(
        tool_use_id="t1",
        tool_name="propose_skill",
        tool_input=_good_input("blocked_y"),
    )
    assert event.is_error is True
    async with sm() as session:
        entry = await SkillRepo(session).get_by_name("blocked_y")
        assert entry is None


async def test_guardrail_passes_with_capability_and_yolo(
    sm_path: tuple[async_sessionmaker[AsyncSession], Path],
    tmp_path: Path,
) -> None:
    """enable_self_mod=True + yolo=True → REQUIRE_APPROVAL + auto_approve → 放行;
    tool 跑通,skill row 写入。"""
    sm, db_path = sm_path
    hooks = AuditHookManager(sm)
    capabilities = Capabilities(enable_self_mod=True, yolo=True)
    mgr = CheckpointManager(
        sessionmaker=sm,
        db_path=db_path,
        checkpoint_dir=tmp_path / "ckpt",
        config_files=[tmp_path / "nx.yaml"],
        cwd=tmp_path / "not_git",
        audit_hooks=hooks,
    )
    svc = SkillProposeService(sessionmaker=sm, audit_hooks=hooks, checkpoint_manager=mgr)
    tool = _make_tool()
    tool.attach_service(svc)

    engine = GuardrailEngine.with_defaults(capabilities=capabilities)
    approval = ApprovalPolicy(capabilities=capabilities)
    exec_svc = ToolExecutionService(
        {"propose_skill": tool},
        guardrail_engine=engine,
        approval_policy=approval,
        audit_hooks=hooks,
    )
    event = await exec_svc.execute_tool_call(
        tool_use_id="t1",
        tool_name="propose_skill",
        tool_input=_good_input("approved_z"),
    )
    assert event.is_error is False
    async with sm() as session:
        entry = await SkillRepo(session).get_by_name("approved_z")
        assert entry is not None
        assert entry.enabled is False

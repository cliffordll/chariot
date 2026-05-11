"""B5 wave 3 step 1 —— `Capabilities` + `CapabilityRepo` + capability gate 翻转。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.config import Capabilities, ConfigError
from chariot.database.session import dispose_db, init_db
from chariot.guardrails import GuardrailEngine, Verdict
from chariot.guardrails.approval import ApprovalPolicy
from chariot.repos.capability_repo import CapabilityRepo


@pytest_asyncio.fixture
async def session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    sm = await init_db(tmp_path / "caps.db")
    async with sm() as session:
        yield session
    await dispose_db()


# ---- CapabilityRepo ----


async def test_seeded_two_rows_default_disabled(session: AsyncSession) -> None:
    """migration v21 种入 enable_self_mod + yolo,默认都 disabled。"""
    entries = await CapabilityRepo(session).list_entries()
    names = {e.name for e in entries}
    assert names == {"enable_self_mod", "yolo"}
    for e in entries:
        assert e.enabled is False


async def test_set_enabled_persists(session: AsyncSession) -> None:
    repo = CapabilityRepo(session)
    await repo.set_enabled("enable_self_mod", True)
    entry = await repo.get_entry("enable_self_mod")
    assert entry is not None
    assert entry.enabled is True


async def test_set_unknown_capability_raises(session: AsyncSession) -> None:
    repo = CapabilityRepo(session)
    with pytest.raises(ConfigError, match="unknown capability"):
        await repo.set_enabled("not_a_real_cap", True)


# ---- Capabilities.from_db ----


async def test_capabilities_from_db_reads_enable_self_mod(session: AsyncSession) -> None:
    repo = CapabilityRepo(session)
    await repo.set_enabled("enable_self_mod", True)
    caps = await Capabilities.from_db(session, yolo=False)
    assert caps.enable_self_mod is True
    assert caps.yolo is False


async def test_capabilities_from_db_yolo_is_param_only(session: AsyncSession) -> None:
    """DB 里 yolo row 即使 enabled,from_db 也走 param —— per-process 语义。"""
    repo = CapabilityRepo(session)
    await repo.set_enabled("yolo", True)
    caps = await Capabilities.from_db(session, yolo=False)
    # from_db 不读 DB 的 yolo,只看 param
    assert caps.yolo is False
    caps_param = await Capabilities.from_db(session, yolo=True)
    assert caps_param.yolo is True


# ---- capability gate translation ----


def test_self_modify_chariot_deny_without_capability() -> None:
    """capabilities=None / enable_self_mod=False → DENY 保持。"""
    engine = GuardrailEngine.with_defaults()
    v = engine.evaluate(tool_name="write_file", args={"path": "chariot/foo.py"})
    assert v.rule_id == "self_modify_chariot"
    assert v.verdict == Verdict.DENY


def test_self_modify_chariot_translates_to_require_approval_when_enabled() -> None:
    """capabilities.enable_self_mod=True → 同样命中,但 verdict → REQUIRE_APPROVAL。"""
    caps = Capabilities(enable_self_mod=True)
    engine = GuardrailEngine.with_defaults(capabilities=caps)
    v = engine.evaluate(tool_name="write_file", args={"path": "chariot/foo.py"})
    assert v.rule_id == "self_modify_chariot"
    assert v.verdict == Verdict.REQUIRE_APPROVAL


def test_other_deny_rules_not_affected_by_capability() -> None:
    """非 self_modify_chariot 的 DENY 规则,即使 enable_self_mod=True 也仍 DENY。"""
    caps = Capabilities(enable_self_mod=True)
    engine = GuardrailEngine.with_defaults(capabilities=caps)
    v = engine.evaluate(tool_name="shell_exec", args={"command": "rm -rf /"})
    assert v.rule_id == "shell_rm_rf"
    assert v.verdict == Verdict.DENY


def test_set_capabilities_runtime_swap() -> None:
    """运行时切 capabilities,verdict 翻转。"""
    engine = GuardrailEngine.with_defaults()
    v1 = engine.evaluate(tool_name="write_file", args={"path": "chariot/x.py"})
    assert v1.verdict == Verdict.DENY
    engine.set_capabilities(Capabilities(enable_self_mod=True))
    v2 = engine.evaluate(tool_name="write_file", args={"path": "chariot/y.py"})
    assert v2.verdict == Verdict.REQUIRE_APPROVAL


# ---- ApprovalPolicy 接 capabilities ----


def test_approval_policy_yolo_from_capabilities() -> None:
    caps = Capabilities(yolo=True)
    policy = ApprovalPolicy(capabilities=caps)
    assert policy.yolo is True
    assert policy.auto_approve("shell_exec") is True


def test_approval_policy_yolo_kwarg_works_too() -> None:
    policy = ApprovalPolicy(yolo=True)
    assert policy.yolo is True
    assert policy.auto_approve("shell_exec") is True


def test_approval_policy_default_no_approve() -> None:
    policy = ApprovalPolicy()
    assert policy.yolo is False
    assert policy.auto_approve("shell_exec") is False

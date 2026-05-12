"""B6 wave 1 step 1a —— `SkillRegistry` builtin + DB union + 优先级。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chariot.database.session import dispose_db, init_db
from chariot.repos.skill_repo import SkillRepo
from chariot.skills import BaseSkill, BuiltinSkill, SkillManifest, SkillRegistry, ToolFilter


@pytest_asyncio.fixture
async def sm(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    sessionmaker = await init_db(tmp_path / "skills.db")
    try:
        yield sessionmaker
    finally:
        await dispose_db()


# ---- load 路径 ----


async def test_load_builtin_only_no_sessionmaker() -> None:
    """sessionmaker=None → 只加载 builtin,无 DB skill。"""
    registry = await SkillRegistry.load(sessionmaker=None)
    names = {s.name for s in registry.list_all()}
    assert {"code_review", "debug_helper", "git_committer"}.issubset(names)
    assert registry.db_names == []


async def test_load_builtin_and_empty_db(sm: async_sessionmaker[AsyncSession]) -> None:
    """DB 空表 → 只 builtin。"""
    registry = await SkillRegistry.load(sm)
    assert registry.db_names == []
    assert len(registry.list_all()) >= 3


async def test_db_skill_overrides_builtin(sm: async_sessionmaker[AsyncSession]) -> None:
    """DB 同名 skill 覆盖 builtin。"""
    # 装一条跟 builtin code_review 同名,但 prompt 不一样的 DB skill
    custom_yaml = """
schema_version: 1
name: code_review
description: USER OVERRIDE
prompt: custom user prompt
"""
    async with sm() as session:
        await SkillRepo(session).create(
            name="code_review",
            description="USER OVERRIDE",
            content=custom_yaml,
            enabled=True,
        )
    registry = await SkillRegistry.load(sm)
    s = registry.get("code_review")
    assert s is not None
    assert s.source == "db"
    assert s.manifest.description == "USER OVERRIDE"
    assert "custom user prompt" in s.manifest.prompt


async def test_db_skill_disabled_excluded_from_list_enabled(
    sm: async_sessionmaker[AsyncSession],
) -> None:
    yaml_text = """
schema_version: 1
name: turned_off
description: disabled sample
prompt: x
"""
    async with sm() as session:
        await SkillRepo(session).create(
            name="turned_off",
            content=yaml_text,
            enabled=False,
        )
    registry = await SkillRegistry.load(sm)
    enabled_names = {s.name for s in registry.list_enabled()}
    all_names = {s.name for s in registry.list_all()}
    assert "turned_off" in all_names
    assert "turned_off" not in enabled_names


async def test_db_skill_corrupt_yaml_silent_skip(sm: async_sessionmaker[AsyncSession]) -> None:
    """DB 行里的 content 是坏 YAML → registry 加载时 skip 那条,不阻其它装载。"""
    async with sm() as session:
        await SkillRepo(session).create(
            name="broken",
            content="this is not valid YAML: : : :",
            enabled=True,
        )
    registry = await SkillRegistry.load(sm)
    assert registry.get("broken") is None
    # builtin 仍然加载
    assert registry.get("code_review") is not None


# ---- BaseSkill helpers ----


def test_prompt_block_wraps_with_skill_tag() -> None:
    manifest = SkillManifest(
        schema_version=1,
        name="x",
        version="1.0.0",
        description="d",
        prompt="hello",
    )
    s: BaseSkill = BuiltinSkill(manifest)
    block = s.prompt_block()
    assert block.startswith('<skill name="x">')
    assert "hello" in block
    assert block.endswith("</skill>")


def test_tool_filter_default_passes_all() -> None:
    """allowed=None + forbidden=[] → 不过滤。"""
    f = ToolFilter()
    assert f.apply(["a", "b", "c"]) == ["a", "b", "c"]


def test_tool_filter_allowed_intersects() -> None:
    f = ToolFilter(allowed=["a", "c"])
    assert f.apply(["a", "b", "c", "d"]) == ["a", "c"]


def test_tool_filter_forbidden_removes_even_from_allowed() -> None:
    f = ToolFilter(allowed=["a", "b"], forbidden=["a"])
    assert f.apply(["a", "b", "c"]) == ["b"]


def test_tool_filter_forbidden_only() -> None:
    f = ToolFilter(forbidden=["shell_exec"])
    assert f.apply(["read_file", "shell_exec", "list_dir"]) == ["read_file", "list_dir"]

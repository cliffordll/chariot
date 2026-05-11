"""B6 wave 2 —— `SkillActivator` 注入 system + tool 过滤。"""

from __future__ import annotations

from chariot.agent.chat_request import ChatRequest, Message, ToolSchema
from chariot.skills.activator import SkillActivator
from chariot.skills.base import BuiltinSkill, SkillManifest


def _make_skill(
    name: str = "tester",
    prompt: str = "be helpful",
    allowed: list[str] | None = None,
    forbidden: list[str] | None = None,
) -> BuiltinSkill:
    return BuiltinSkill(
        SkillManifest(
            schema_version=1,
            name=name,
            version="0.1.0",
            description="d",
            prompt=prompt,
            allowed_tools=allowed,
            forbidden_tools=forbidden or [],
        )
    )


def _make_req(*, system: str | None = None, tools: list[ToolSchema] | None = None) -> ChatRequest:
    return ChatRequest(
        provider_name="mock",
        messages=[Message(role="user", content="hi")],
        system=system,
        tools=tools,
    )


# ---- system 注入 ----


def test_activate_injects_prompt_block_into_empty_system() -> None:
    req = _make_req()
    skill = _make_skill(name="t", prompt="DO IT")
    out = SkillActivator.activate(req, skill)
    assert isinstance(out.system, str)
    assert "DO IT" in out.system
    assert '<skill name="t">' in out.system
    assert out.system.rstrip().endswith("</skill>")


def test_activate_appends_to_existing_system_text() -> None:
    req = _make_req(system="EXISTING BUNDLE TEXT")
    skill = _make_skill(name="t", prompt="EXTRA")
    out = SkillActivator.activate(req, skill)
    assert isinstance(out.system, str)
    assert out.system.startswith("EXISTING BUNDLE TEXT")
    assert "EXTRA" in out.system
    # 两段之间空一行
    assert "\n\n<skill" in out.system


def test_activate_appends_to_anthropic_system_list_form() -> None:
    """system 是 Anthropic block list 形态时,追加一条 text block。"""
    req = _make_req()
    req_with_list = req.__class__(
        provider_name="mock",
        messages=req.messages,
        system=[{"type": "text", "text": "BLOCK_A"}],
    )
    skill = _make_skill(prompt="P")
    out = SkillActivator.activate(req_with_list, skill)
    assert isinstance(out.system, list)
    assert len(out.system) == 2
    assert out.system[0]["text"] == "BLOCK_A"
    assert "P" in str(out.system[1]["text"])


# ---- tool filter ----


def _ts(name: str) -> ToolSchema:
    return ToolSchema(name=name, description="x", input_schema={})


def test_activate_tools_none_stays_none() -> None:
    req = _make_req()
    skill = _make_skill(allowed=["read_file"])
    out = SkillActivator.activate(req, skill)
    assert out.tools is None  # _inject_default_tools 阶段还会再处理;activator 不假定


def test_activate_filters_by_allowed_tools() -> None:
    req = _make_req(tools=[_ts("read_file"), _ts("shell_exec"), _ts("list_dir")])
    skill = _make_skill(allowed=["read_file", "list_dir"])
    out = SkillActivator.activate(req, skill)
    assert out.tools is not None
    assert {t.name for t in out.tools} == {"read_file", "list_dir"}


def test_activate_filters_by_forbidden_tools_even_in_allowed() -> None:
    """forbidden 优先于 allowed:即使在 allowed 里,也剔除。"""
    req = _make_req(tools=[_ts("read_file"), _ts("shell_exec")])
    skill = _make_skill(allowed=["read_file", "shell_exec"], forbidden=["shell_exec"])
    out = SkillActivator.activate(req, skill)
    assert out.tools is not None
    assert {t.name for t in out.tools} == {"read_file"}


def test_activate_forbidden_only_no_allowed_list() -> None:
    """allowed=None → 不限制白名单;只剔 forbidden。"""
    req = _make_req(tools=[_ts("read_file"), _ts("shell_exec"), _ts("write_file")])
    skill = _make_skill(forbidden=["shell_exec"])
    out = SkillActivator.activate(req, skill)
    assert out.tools is not None
    assert {t.name for t in out.tools} == {"read_file", "write_file"}


def test_activate_preserves_tool_order() -> None:
    req = _make_req(tools=[_ts("c"), _ts("a"), _ts("b")])
    skill = _make_skill(allowed=["a", "b", "c"])
    out = SkillActivator.activate(req, skill)
    assert out.tools is not None
    assert [t.name for t in out.tools] == ["c", "a", "b"]

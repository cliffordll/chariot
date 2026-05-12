"""B6 wave 1 step 1a —— `SkillLoader` YAML 解析 + jsonschema 校验。"""

from __future__ import annotations

from pathlib import Path

import pytest

from chariot.skills.loader import SkillLoader, SkillManifestError

# ---- 正面用例 ----


def test_parse_minimal_yaml() -> None:
    text = """
schema_version: 1
name: smoke
description: minimal smoke skill
prompt: hello world
"""
    m = SkillLoader.parse_yaml(text)
    assert m.name == "smoke"
    assert m.description == "minimal smoke skill"
    assert m.prompt == "hello world"
    assert m.version == "0.1.0"  # default
    assert m.allowed_tools is None
    assert m.forbidden_tools == []
    assert m.tags == []
    assert m.enabled is True


def test_parse_full_yaml() -> None:
    text = """
schema_version: 1
name: full_one
version: "2.3.4"
description: a fully specified skill
author: tester
prompt: |
  multi line
  prompt body
allowed_tools:
  - read_file
  - list_dir
forbidden_tools:
  - shell_exec
tags:
  - test
  - sample
"""
    m = SkillLoader.parse_yaml(text)
    assert m.name == "full_one"
    assert m.version == "2.3.4"
    assert "multi line" in m.prompt
    assert m.allowed_tools == ["read_file", "list_dir"]
    assert m.forbidden_tools == ["shell_exec"]
    assert m.tags == ["test", "sample"]


def test_load_builtin_returns_three_samples() -> None:
    """B6 wave 1 三条内置 sample(code_review / debug_helper / git_committer)都装。"""
    skills = SkillLoader.load_builtin()
    names = {s.name for s in skills}
    assert {"code_review", "debug_helper", "git_committer"}.issubset(names)
    for s in skills:
        assert s.source == "builtin"
        assert s.enabled is True


# ---- 负面用例 ----


def test_missing_required_raises() -> None:
    text = """
schema_version: 1
description: missing-name
prompt: x
"""
    with pytest.raises(SkillManifestError, match="schema"):
        SkillLoader.parse_yaml(text)


def test_invalid_name_pattern_raises() -> None:
    """name 必须是 `[a-z][a-z0-9_]*` —— 大写 / 数字开头 / 横杠都拒。"""
    bad_names = ["CodeReview", "1skill", "code-review"]
    for name in bad_names:
        text = f"""
schema_version: 1
name: {name}
description: x
prompt: x
"""
        with pytest.raises(SkillManifestError):
            SkillLoader.parse_yaml(text)


def test_unknown_field_raises() -> None:
    """additionalProperties: False —— 未知字段拒,避免 typo 静默丢失。"""
    text = """
schema_version: 1
name: ok_name
description: x
prompt: x
foobar: oops
"""
    with pytest.raises(SkillManifestError):
        SkillLoader.parse_yaml(text)


def test_load_from_dir_detects_duplicates(tmp_path: Path) -> None:
    """同目录两个 YAML 用同一个 name → 抛。"""
    a = tmp_path / "a.yaml"
    a.write_text(
        """
schema_version: 1
name: dup
description: a
prompt: x
""",
        encoding="utf-8",
    )
    b = tmp_path / "b.yaml"
    b.write_text(
        """
schema_version: 1
name: dup
description: b
prompt: y
""",
        encoding="utf-8",
    )
    with pytest.raises(SkillManifestError, match="重名"):
        SkillLoader.load_from_dir(tmp_path)


def test_load_from_dir_empty_returns_empty(tmp_path: Path) -> None:
    out = SkillLoader.load_from_dir(tmp_path)
    assert out == []


def test_load_from_dir_missing_returns_empty(tmp_path: Path) -> None:
    out = SkillLoader.load_from_dir(tmp_path / "does_not_exist")
    assert out == []


def test_yaml_syntax_error_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(": : :\n", encoding="utf-8")
    with pytest.raises(SkillManifestError):
        SkillLoader.load_from_dir(tmp_path)


def test_schema_exposed() -> None:
    schema = SkillLoader.schema()
    assert schema["required"] == ["schema_version", "name", "description", "prompt"]

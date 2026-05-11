"""GoldenTaskLoader 文件读取 + schema 校验。"""

from __future__ import annotations

from pathlib import Path

import pytest

from chariot.eval.loader import GoldenTaskLoader, GoldenTaskLoadError


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_load_file_minimum_valid(tmp_path: Path) -> None:
    f = tmp_path / "smoke.yaml"
    _write(
        f,
        """
task_id: smoke_oneshot
prompt: say hi
verifier_type: exact_match
expected:
  contains: [hi]
""",
    )
    task = GoldenTaskLoader.load_file(f)
    assert task.task_id == "smoke_oneshot"
    assert task.verifier_type == "exact_match"
    assert task.expected == {"contains": ["hi"]}
    assert task.category == "uncategorized"
    assert task.max_iterations == 10


def test_load_file_full_fields(tmp_path: Path) -> None:
    f = tmp_path / "file_read.yaml"
    _write(
        f,
        """
task_id: file_read_pyproject
prompt: 看一下 pyproject.toml
verifier_type: tool_called
expected:
  tool: read_file
  args_subset:
    path: pyproject.toml
category: file
description: agent 该用 read_file
max_iterations: 5
model: claude-sonnet-4-6
system: null
""",
    )
    task = GoldenTaskLoader.load_file(f)
    assert task.category == "file"
    assert task.max_iterations == 5
    assert task.model == "claude-sonnet-4-6"
    assert task.expected["args_subset"]["path"] == "pyproject.toml"


def test_load_file_missing_required_field_raises(tmp_path: Path) -> None:
    f = tmp_path / "bad.yaml"
    _write(f, "task_id: x\nprompt: y\n")  # 缺 verifier_type + expected
    with pytest.raises(GoldenTaskLoadError, match="missing required field"):
        GoldenTaskLoader.load_file(f)


def test_load_file_unsupported_verifier_raises(tmp_path: Path) -> None:
    f = tmp_path / "unsupp.yaml"
    _write(
        f,
        """
task_id: x
prompt: y
verifier_type: bogus_verifier
expected: {}
""",
    )
    with pytest.raises(GoldenTaskLoadError, match="unsupported verifier_type"):
        GoldenTaskLoader.load_file(f)


def test_load_file_expected_must_be_dict(tmp_path: Path) -> None:
    f = tmp_path / "bad2.yaml"
    _write(
        f,
        """
task_id: x
prompt: y
verifier_type: exact_match
expected: not_a_dict
""",
    )
    with pytest.raises(GoldenTaskLoadError, match="'expected' must be a mapping"):
        GoldenTaskLoader.load_file(f)


def test_load_file_invalid_yaml_raises(tmp_path: Path) -> None:
    f = tmp_path / "bad.yaml"
    _write(f, "task_id: x\n  bad indent:\n - [")
    with pytest.raises(GoldenTaskLoadError, match="invalid YAML"):
        GoldenTaskLoader.load_file(f)


def test_load_file_not_found_raises(tmp_path: Path) -> None:
    with pytest.raises(GoldenTaskLoadError, match="not found"):
        GoldenTaskLoader.load_file(tmp_path / "ghost.yaml")


def test_load_dir_returns_sorted_tasks(tmp_path: Path) -> None:
    _write(tmp_path / "b.yaml", "task_id: b\nprompt: p\nverifier_type: exact_match\nexpected: {contains: []}\n")
    _write(tmp_path / "a.yaml", "task_id: a\nprompt: p\nverifier_type: exact_match\nexpected: {contains: []}\n")
    tasks = GoldenTaskLoader.load_dir(tmp_path)
    assert [t.task_id for t in tasks] == ["a", "b"]


def test_load_dir_recurses_subdirs(tmp_path: Path) -> None:
    sub = tmp_path / "nested"
    sub.mkdir()
    _write(sub / "deep.yaml", "task_id: deep\nprompt: p\nverifier_type: exact_match\nexpected: {contains: []}\n")
    _write(tmp_path / "top.yaml", "task_id: top\nprompt: p\nverifier_type: exact_match\nexpected: {contains: []}\n")
    tasks = GoldenTaskLoader.load_dir(tmp_path)
    assert {t.task_id for t in tasks} == {"deep", "top"}


def test_load_dir_duplicate_task_id_raises(tmp_path: Path) -> None:
    _write(tmp_path / "a.yaml", "task_id: same\nprompt: p\nverifier_type: exact_match\nexpected: {contains: []}\n")
    _write(tmp_path / "b.yaml", "task_id: same\nprompt: p\nverifier_type: exact_match\nexpected: {contains: []}\n")
    with pytest.raises(GoldenTaskLoadError, match="duplicate task_id"):
        GoldenTaskLoader.load_dir(tmp_path)


def test_load_dir_skips_hidden_files(tmp_path: Path) -> None:
    _write(tmp_path / ".hidden.yaml", "task_id: hidden\nprompt: p\nverifier_type: exact_match\nexpected: {}\n")
    _write(tmp_path / "real.yaml", "task_id: real\nprompt: p\nverifier_type: exact_match\nexpected: {contains: []}\n")
    tasks = GoldenTaskLoader.load_dir(tmp_path)
    assert [t.task_id for t in tasks] == ["real"]


def test_load_dir_not_found_raises(tmp_path: Path) -> None:
    with pytest.raises(GoldenTaskLoadError, match="not found"):
        GoldenTaskLoader.load_dir(tmp_path / "ghost_dir")

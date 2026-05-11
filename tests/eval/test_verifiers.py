"""4 个 verifier 实现的边界 / happy path / 错误路径覆盖。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import pytest

from chariot.eval.verifiers import VERIFIERS
from chariot.eval.verifiers.exact_match import ExactMatchVerifier
from chariot.eval.verifiers.file_state import FileStateVerifier
from chariot.eval.verifiers.output_schema import OutputSchemaVerifier
from chariot.eval.verifiers.tool_called import ToolCalledVerifier
from chariot.models.eval import GoldenTask, RunRecord, Verdict


def _task(verifier_type: str, expected: dict[str, Any]) -> GoldenTask:
    return GoldenTask(task_id="t", prompt="p", verifier_type=verifier_type, expected=expected)


def _record(*, final_response: str = "", tool_calls: list[dict[str, Any]] | None = None) -> RunRecord:
    return RunRecord(
        task_id="t",
        verdict=Verdict.PASS,
        final_response=final_response,
        tool_calls=tool_calls or [],
    )


# -------- registry 完整性 --------


def test_registry_has_four_verifiers() -> None:
    assert set(VERIFIERS.keys()) == {"exact_match", "tool_called", "file_state", "output_schema"}


# -------- ExactMatchVerifier --------


class TestExactMatch:
    def test_all_substrings_present_passes(self) -> None:
        task = _task("exact_match", {"contains": ["class", "method"]})
        result = ExactMatchVerifier().verify(task, _record(final_response="A class has methods"))
        assert result.verdict is Verdict.PASS

    def test_missing_substring_fails(self) -> None:
        task = _task("exact_match", {"contains": ["class", "method"]})
        result = ExactMatchVerifier().verify(task, _record(final_response="just class"))
        assert result.verdict is Verdict.FAIL
        assert "method" in result.reason

    def test_case_insensitive(self) -> None:
        task = _task("exact_match", {"contains": ["HELLO"], "case_insensitive": True})
        result = ExactMatchVerifier().verify(task, _record(final_response="hello world"))
        assert result.verdict is Verdict.PASS

    def test_case_sensitive_default(self) -> None:
        task = _task("exact_match", {"contains": ["HELLO"]})
        result = ExactMatchVerifier().verify(task, _record(final_response="hello world"))
        assert result.verdict is Verdict.FAIL

    def test_empty_contains_errors(self) -> None:
        result = ExactMatchVerifier().verify(_task("exact_match", {"contains": []}), _record())
        assert result.verdict is Verdict.ERROR

    def test_contains_non_string_errors(self) -> None:
        result = ExactMatchVerifier().verify(_task("exact_match", {"contains": [123]}), _record())
        assert result.verdict is Verdict.ERROR


# -------- ToolCalledVerifier --------


class TestToolCalled:
    def test_tool_called_no_args_required(self) -> None:
        task = _task("tool_called", {"tool": "read_file"})
        result = ToolCalledVerifier().verify(
            task, _record(tool_calls=[{"tool_name": "read_file", "arguments": {"path": "x"}}])
        )
        assert result.verdict is Verdict.PASS

    def test_args_subset_must_match(self) -> None:
        task = _task("tool_called", {"tool": "read_file", "args_subset": {"path": "pyproject.toml"}})
        # 命中:有 path=pyproject.toml + 多余 limit 字段(superset 允许)
        ok = ToolCalledVerifier().verify(
            task,
            _record(tool_calls=[{"tool_name": "read_file", "arguments": {"path": "pyproject.toml", "limit": 100}}]),
        )
        assert ok.verdict is Verdict.PASS

    def test_args_subset_value_mismatch_fails(self) -> None:
        task = _task("tool_called", {"tool": "read_file", "args_subset": {"path": "pyproject.toml"}})
        bad = ToolCalledVerifier().verify(
            task, _record(tool_calls=[{"tool_name": "read_file", "arguments": {"path": "README.md"}}])
        )
        assert bad.verdict is Verdict.FAIL
        assert "args" in bad.reason

    def test_no_tool_calls_fails(self) -> None:
        task = _task("tool_called", {"tool": "read_file"})
        result = ToolCalledVerifier().verify(task, _record(tool_calls=[]))
        assert result.verdict is Verdict.FAIL

    def test_wrong_tool_called_fails(self) -> None:
        task = _task("tool_called", {"tool": "read_file"})
        result = ToolCalledVerifier().verify(
            task, _record(tool_calls=[{"tool_name": "list_dir", "arguments": {"path": "."}}])
        )
        assert result.verdict is Verdict.FAIL
        assert "list_dir" in result.reason

    def test_multiple_calls_one_matches(self) -> None:
        """多次 tool call,至少一项匹配即 PASS。"""
        task = _task("tool_called", {"tool": "read_file", "args_subset": {"path": "x"}})
        result = ToolCalledVerifier().verify(
            task,
            _record(
                tool_calls=[
                    {"tool_name": "list_dir", "arguments": {"path": "."}},
                    {"tool_name": "read_file", "arguments": {"path": "x"}},
                ]
            ),
        )
        assert result.verdict is Verdict.PASS

    def test_missing_tool_field_errors(self) -> None:
        result = ToolCalledVerifier().verify(_task("tool_called", {}), _record())
        assert result.verdict is Verdict.ERROR

    def test_args_subset_not_dict_errors(self) -> None:
        task = _task("tool_called", {"tool": "x", "args_subset": "not a dict"})
        result = ToolCalledVerifier().verify(task, _record(tool_calls=[{"tool_name": "x", "arguments": {}}]))
        assert result.verdict is Verdict.ERROR


# -------- FileStateVerifier --------


class TestFileState:
    def test_exists_pass(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "x.txt").write_text("hi", encoding="utf-8")
        result = FileStateVerifier().verify(_task("file_state", {"path": "x.txt"}), _record())
        assert result.verdict is Verdict.PASS

    def test_missing_file_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = FileStateVerifier().verify(_task("file_state", {"path": "ghost.txt"}), _record())
        assert result.verdict is Verdict.FAIL

    def test_exists_false_pass(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = FileStateVerifier().verify(_task("file_state", {"path": "ghost.txt", "exists": False}), _record())
        assert result.verdict is Verdict.PASS

    def test_exists_false_but_file_exists_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "x.txt").write_text("hi", encoding="utf-8")
        result = FileStateVerifier().verify(_task("file_state", {"path": "x.txt", "exists": False}), _record())
        assert result.verdict is Verdict.FAIL

    def test_contains_pass(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "x.txt").write_text("evaluated and ready", encoding="utf-8")
        task = _task("file_state", {"path": "x.txt", "contains": "evaluated"})
        assert FileStateVerifier().verify(task, _record()).verdict is Verdict.PASS

    def test_contains_fail(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "x.txt").write_text("nothing here", encoding="utf-8")
        task = _task("file_state", {"path": "x.txt", "contains": "evaluated"})
        result = FileStateVerifier().verify(task, _record())
        assert result.verdict is Verdict.FAIL

    def test_missing_path_errors(self) -> None:
        result = FileStateVerifier().verify(_task("file_state", {}), _record())
        assert result.verdict is Verdict.ERROR


# -------- OutputSchemaVerifier --------


class TestOutputSchema:
    _SIMPLE_SCHEMA: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
        "required": ["name"],
    }

    def test_valid_json_passes(self) -> None:
        task = _task("output_schema", {"schema": self._SIMPLE_SCHEMA})
        result = OutputSchemaVerifier().verify(task, _record(final_response='{"name": "alice", "age": 30}'))
        assert result.verdict is Verdict.PASS

    def test_missing_required_fails(self) -> None:
        task = _task("output_schema", {"schema": self._SIMPLE_SCHEMA})
        result = OutputSchemaVerifier().verify(task, _record(final_response='{"age": 30}'))
        assert result.verdict is Verdict.FAIL

    def test_wrong_type_fails(self) -> None:
        task = _task("output_schema", {"schema": self._SIMPLE_SCHEMA})
        result = OutputSchemaVerifier().verify(task, _record(final_response='{"name": "alice", "age": "thirty"}'))
        assert result.verdict is Verdict.FAIL

    def test_invalid_json_fails(self) -> None:
        task = _task("output_schema", {"schema": self._SIMPLE_SCHEMA})
        result = OutputSchemaVerifier().verify(task, _record(final_response="{not valid json"))
        assert result.verdict is Verdict.FAIL

    def test_decode_json_in_fence(self) -> None:
        task = _task("output_schema", {"schema": self._SIMPLE_SCHEMA, "decode": "json_in_fence"})
        response = 'Here\'s the result:\n```json\n{"name": "alice"}\n```\nAll good.'
        result = OutputSchemaVerifier().verify(task, _record(final_response=response))
        assert result.verdict is Verdict.PASS

    def test_decode_json_in_fence_no_fence_fails(self) -> None:
        task = _task("output_schema", {"schema": self._SIMPLE_SCHEMA, "decode": "json_in_fence"})
        result = OutputSchemaVerifier().verify(task, _record(final_response='{"name": "alice"}'))
        assert result.verdict is Verdict.FAIL

    def test_unsupported_decode_mode_errors(self) -> None:
        task = _task("output_schema", {"schema": self._SIMPLE_SCHEMA, "decode": "bogus"})
        result = OutputSchemaVerifier().verify(task, _record(final_response="{}"))
        assert result.verdict is Verdict.ERROR

    def test_invalid_schema_errors(self) -> None:
        task = _task("output_schema", {"schema": {"type": "not_a_real_type"}})
        result = OutputSchemaVerifier().verify(task, _record(final_response='"x"'))
        assert result.verdict is Verdict.ERROR

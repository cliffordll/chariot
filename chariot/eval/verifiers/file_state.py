"""FileStateVerifier —— 跑完后磁盘上 expected.path 文件状态符合。

`expected` schema:
- `path: str` —— 必填;文件路径,相对当前 cwd 解析
- `exists: bool` —— 可选;默认 true。true=必须存在;false=必须不存在
- `contains: str` —— 可选;若 exists=true,内容必须包含该子串(UTF-8 解码)

不做大小 / mtime / permission 检查 —— B2 只验"事情发生了"。更严格的状态
比对留给后续 verifier 类型扩展。
"""

from __future__ import annotations

from pathlib import Path

from chariot.eval.verifiers.base import BaseVerifier
from chariot.models.eval import GoldenTask, RunRecord, Verdict, VerifierResult


class FileStateVerifier(BaseVerifier):
    def verify(self, task: GoldenTask, record: RunRecord) -> VerifierResult:
        del record  # file_state 不看 RunRecord,直接看磁盘
        raw_path = task.expected.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            return VerifierResult(
                verdict=Verdict.ERROR,
                reason="file_state: 'expected.path' must be a non-empty string",
            )
        expect_exists = bool(task.expected.get("exists", True))
        expect_contains = task.expected.get("contains")
        if expect_contains is not None and not isinstance(expect_contains, str):
            return VerifierResult(
                verdict=Verdict.ERROR,
                reason="file_state: 'contains' must be a string (or omitted)",
            )

        path = Path(raw_path)
        exists = path.is_file()
        if expect_exists and not exists:
            return VerifierResult(
                verdict=Verdict.FAIL,
                reason=f"file does not exist: {raw_path}",
            )
        if not expect_exists and exists:
            return VerifierResult(
                verdict=Verdict.FAIL,
                reason=f"file unexpectedly exists: {raw_path}",
            )
        if expect_contains is not None:
            try:
                content = path.read_text(encoding="utf-8")
            except OSError as exc:
                return VerifierResult(
                    verdict=Verdict.ERROR,
                    reason=f"file_state: failed to read {raw_path}: {exc}",
                )
            except UnicodeDecodeError as exc:
                return VerifierResult(
                    verdict=Verdict.ERROR,
                    reason=f"file_state: {raw_path} is not valid UTF-8: {exc}",
                )
            if expect_contains not in content:
                return VerifierResult(
                    verdict=Verdict.FAIL,
                    reason=f"{raw_path} does not contain {expect_contains!r}",
                )
        return VerifierResult(verdict=Verdict.PASS)

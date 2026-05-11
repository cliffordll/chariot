"""Verifier registry。

四个真实现(wave 3 起齐):
- `exact_match`   — final_response 必须包含指定子串(AND)
- `tool_called`   — tool_calls 至少一项 name + args_subset 匹配
- `file_state`    — 跑完后 expected.path 文件状态符合
- `output_schema` — final_response 解析后符合 JSON Schema

新增 verifier 类型:实现 `BaseVerifier`,注册到 `VERIFIERS` dict,在
`chariot/eval/loader.py:_SUPPORTED_VERIFIERS` 同步追加。
"""

from chariot.eval.verifiers.base import BaseVerifier
from chariot.eval.verifiers.exact_match import ExactMatchVerifier
from chariot.eval.verifiers.file_state import FileStateVerifier
from chariot.eval.verifiers.output_schema import OutputSchemaVerifier
from chariot.eval.verifiers.tool_called import ToolCalledVerifier

VERIFIERS: dict[str, type[BaseVerifier]] = {
    "exact_match": ExactMatchVerifier,
    "tool_called": ToolCalledVerifier,
    "file_state": FileStateVerifier,
    "output_schema": OutputSchemaVerifier,
}
"""verifier_type → 实现类的查询表。Runner 按 task.verifier_type 取类后实例化。"""


__all__ = [
    "VERIFIERS",
    "BaseVerifier",
    "ExactMatchVerifier",
    "FileStateVerifier",
    "OutputSchemaVerifier",
    "ToolCalledVerifier",
]

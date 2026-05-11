"""Verifier registry。

Wave 1:只放 ABC + 空 registry。wave 3 填四个真实现:
- `exact_match`   — final_response 必须包含指定子串(AND)
- `tool_called`   — tool_calls 至少一项 name + args_subset 匹配
- `file_state`    — 跑完后 expected.path 文件状态符合
- `output_schema` — final_response 解析后符合 JSON Schema

新增 verifier 类型时:实现 `BaseVerifier`,注册到 `VERIFIERS` dict。
"""

from chariot.eval.verifiers.base import BaseVerifier

VERIFIERS: dict[str, type[BaseVerifier]] = {}
"""verifier_type → 实现类的查询表;wave 3 填。"""


__all__ = ["VERIFIERS", "BaseVerifier"]

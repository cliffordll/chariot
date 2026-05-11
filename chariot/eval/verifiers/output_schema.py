"""OutputSchemaVerifier —— final_response 解析后符合 JSON Schema。

`expected` schema:
- `schema: dict` —— 必填;JSON Schema(draft 2020-12 默认)
- `decode: str` —— 可选;`json`(默认)/ `json_in_fence`(从 ```json ... ``` 块抽取)
- `case_insensitive: bool` —— N/A,schema 自带

为什么需要这个 verifier:`exact_match` 只能检关键词,`tool_called` 只能检
工具行为。但很多 task 需要的是"结构化输出":比如让 agent 返一段 JSON 报告,
里面的字段、类型、必填项都对得上 schema —— `output_schema` 就是干这个的。
"""

from __future__ import annotations

import json
import re
from typing import Any

import jsonschema

from chariot.eval.verifiers.base import BaseVerifier
from chariot.models.eval import GoldenTask, RunRecord, Verdict, VerifierResult

_FENCE_PATTERN = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


class OutputSchemaVerifier(BaseVerifier):
    def verify(self, task: GoldenTask, record: RunRecord) -> VerifierResult:
        schema = task.expected.get("schema")
        if not isinstance(schema, dict):
            return VerifierResult(
                verdict=Verdict.ERROR,
                reason="output_schema: 'expected.schema' must be a mapping (JSON Schema)",
            )
        decode_mode = str(task.expected.get("decode", "json"))
        if decode_mode not in ("json", "json_in_fence"):
            return VerifierResult(
                verdict=Verdict.ERROR,
                reason=f"output_schema: unsupported decode mode {decode_mode!r}; expected 'json' or 'json_in_fence'",
            )

        raw = record.final_response or ""
        text = self._extract_json_text(raw, mode=decode_mode)
        if text is None:
            return VerifierResult(
                verdict=Verdict.FAIL,
                reason=f"output_schema: no JSON found in final_response (decode={decode_mode})",
            )
        try:
            instance: Any = json.loads(text)
        except json.JSONDecodeError as exc:
            return VerifierResult(
                verdict=Verdict.FAIL,
                reason=f"output_schema: JSON decode failed: {exc}",
            )
        try:
            jsonschema.validate(instance=instance, schema=schema)
        except jsonschema.ValidationError as exc:
            return VerifierResult(
                verdict=Verdict.FAIL,
                reason=f"output_schema: schema validation failed: {exc.message}",
            )
        except jsonschema.SchemaError as exc:
            return VerifierResult(
                verdict=Verdict.ERROR,
                reason=f"output_schema: invalid JSON Schema: {exc.message}",
            )
        return VerifierResult(verdict=Verdict.PASS)

    @staticmethod
    def _extract_json_text(raw: str, *, mode: str) -> str | None:
        if mode == "json":
            return raw.strip() or None
        # json_in_fence:扫 ```json``` 或裸 ``` 块,返第一个非空内容
        for match in _FENCE_PATTERN.finditer(raw):
            inner = match.group(1).strip()
            if inner:
                return inner
        return None

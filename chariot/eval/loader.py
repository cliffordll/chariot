"""YAML → GoldenTask loader。

约定:`tests/golden/<task_id>.yaml`(单文件单 task)。loader 扫目录或读单文件,
都返回 GoldenTask 实例。结构错误立即抛 `GoldenTaskLoadError`(不容错;eval
跑得起来 = 所有 task 已经 schema-valid)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from chariot.models.eval import GoldenTask


class GoldenTaskLoadError(ValueError):
    """YAML schema 错误或文件读不出。"""


class GoldenTaskLoader:
    """YAML / 目录 → GoldenTask 实例。无状态,纯 classmethod。"""

    _REQUIRED_FIELDS = ("task_id", "prompt", "verifier_type", "expected")
    _SUPPORTED_VERIFIERS = ("exact_match", "tool_called", "file_state", "output_schema")

    @classmethod
    def load_file(cls, path: Path) -> GoldenTask:
        """读单个 YAML 文件 → GoldenTask;schema 校验失败抛 GoldenTaskLoadError。"""
        if not path.is_file():
            raise GoldenTaskLoadError(f"golden task file not found: {path}")
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise GoldenTaskLoadError(f"invalid YAML in {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise GoldenTaskLoadError(f"top-level must be a mapping in {path}")
        return cls._dict_to_task(raw, source=path)

    @classmethod
    def load_dir(cls, root: Path) -> list[GoldenTask]:
        """扫目录所有 *.yaml/*.yml,按文件名排序返回 task 列表。

        子目录会递归扫描;隐藏文件(`.` 开头)跳过。schema 错误 fail-fast。
        """
        if not root.is_dir():
            raise GoldenTaskLoadError(f"golden task dir not found: {root}")
        files = sorted([p for p in root.rglob("*") if p.suffix in {".yaml", ".yml"} and not p.name.startswith(".")])
        tasks: list[GoldenTask] = []
        seen_ids: set[str] = set()
        for path in files:
            task = cls.load_file(path)
            if task.task_id in seen_ids:
                raise GoldenTaskLoadError(f"duplicate task_id {task.task_id!r} in {path}")
            seen_ids.add(task.task_id)
            tasks.append(task)
        return tasks

    @classmethod
    def _dict_to_task(cls, raw: dict[str, Any], *, source: Path) -> GoldenTask:
        for key in cls._REQUIRED_FIELDS:
            if key not in raw:
                raise GoldenTaskLoadError(f"missing required field {key!r} in {source}")
        verifier_type = str(raw["verifier_type"])
        if verifier_type not in cls._SUPPORTED_VERIFIERS:
            raise GoldenTaskLoadError(
                f"unsupported verifier_type {verifier_type!r} in {source}; expected one of {cls._SUPPORTED_VERIFIERS}",
            )
        expected = raw.get("expected")
        if not isinstance(expected, dict):
            raise GoldenTaskLoadError(f"'expected' must be a mapping in {source}")
        try:
            return GoldenTask(
                task_id=str(raw["task_id"]),
                prompt=str(raw["prompt"]),
                verifier_type=verifier_type,
                expected=expected,
                category=str(raw.get("category", "uncategorized")),
                description=str(raw.get("description", "")),
                max_iterations=int(raw.get("max_iterations", 10)),
                model=raw.get("model"),
                system=raw.get("system"),
            )
        except (TypeError, ValueError) as exc:
            raise GoldenTaskLoadError(f"invalid GoldenTask fields in {source}: {exc}") from exc

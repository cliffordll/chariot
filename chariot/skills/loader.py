"""Skill manifest loader —— YAML 解析 + jsonschema 校验(B6 wave 1)。

封装策略(CLAUDE.md ⭐):
- 单类 `SkillLoader` 编排 builtin 扫盘 + manifest 解析;模块级零自由函数
- 校验失败抛 `SkillManifestError`,**不** silent skip(避免误读残缺 sample 当合规)
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any, ClassVar, cast

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from chariot.skills.base import BuiltinSkill, SkillManifest

_MANIFEST_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["schema_version", "name", "description", "prompt"],
    "additionalProperties": False,
    "properties": {
        "schema_version": {"type": "integer", "enum": [1]},
        "name": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$", "maxLength": 64},
        "version": {"type": "string", "default": "0.1.0"},
        "description": {"type": "string", "minLength": 1, "maxLength": 240},
        "author": {"type": "string"},
        "prompt": {"type": "string", "minLength": 1, "maxLength": 4000},
        "allowed_tools": {
            "anyOf": [
                {"type": "null"},
                {"type": "array", "items": {"type": "string"}},
            ],
        },
        "forbidden_tools": {"type": "array", "items": {"type": "string"}},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
}


class SkillManifestError(ValueError):
    """YAML 字段不对 / 缺必填 / 类型错;`SkillLoader` 抛,不 swallow。"""


class SkillLoader:
    """Skill YAML 加载器。

    用法::

        skills = SkillLoader.load_builtin()        # 扫 chariot/skills/builtin/*.yaml
        # 单条解析(给 DB skill 用,DB.content 字段就是 YAML 文本)
        manifest = SkillLoader.parse_yaml(yaml_text)
    """

    _validator: ClassVar[Draft202012Validator] = Draft202012Validator(_MANIFEST_SCHEMA)

    @classmethod
    def schema(cls) -> dict[str, Any]:
        """暴露给 sidecar / 桌面 / 测试。"""
        return _MANIFEST_SCHEMA

    @classmethod
    def parse_yaml(cls, text: str, *, enabled: bool = True) -> SkillManifest:
        """单条 YAML 文本 → SkillManifest。jsonschema 校验失败抛
        `SkillManifestError`。

        `enabled`:builtin 默认 True;DB skill 由 caller 传 `SkillRow.enabled`
        覆盖。
        """
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise SkillManifestError(f"YAML 解析失败: {e}") from e
        if not isinstance(data, dict):
            raise SkillManifestError("YAML 顶层必须是 object")
        return cls._dict_to_manifest(cast(dict[str, Any], data), enabled=enabled)

    @classmethod
    def load_builtin(cls) -> list[BuiltinSkill]:
        """扫 `chariot/skills/builtin/*.yaml`,按 name 字典序返回 BuiltinSkill list。

        - 重名 → 抛 `SkillManifestError`(builtin 内必须唯一)
        - 任何文件解析失败 → 抛(避免 silent skip 让用户以为加载了实际没有)
        - 测试场景:用 `load_from_dir(path)` 直接指定目录,绕开 importlib 锚点
        """
        builtin_pkg = "chariot.skills.builtin"
        try:
            base = resources.files(builtin_pkg)
        except (ModuleNotFoundError, FileNotFoundError):
            return []
        if not base.is_dir():
            return []
        # importlib.resources.files() 返回 Traversable;落到磁盘上以遍历
        return cls.load_from_dir(Path(str(base)))

    @classmethod
    def load_from_dir(cls, directory: Path) -> list[BuiltinSkill]:
        """从指定目录扫 *.yaml(给测试 / 第三方扩展用)。"""
        if not directory.exists():
            return []
        skills: list[BuiltinSkill] = []
        seen: set[str] = set()
        for path in sorted(directory.glob("*.yaml")):
            text = path.read_text(encoding="utf-8")
            try:
                manifest = cls.parse_yaml(text, enabled=True)
            except SkillManifestError as e:
                raise SkillManifestError(f"{path.name}: {e}") from e
            if manifest.name in seen:
                raise SkillManifestError(f"builtin skill 重名: {manifest.name} (file: {path.name})")
            seen.add(manifest.name)
            skills.append(BuiltinSkill(manifest))
        return skills

    # ---- 内部 ----

    @classmethod
    def _dict_to_manifest(cls, data: dict[str, Any], *, enabled: bool) -> SkillManifest:
        try:
            cls._validator.validate(data)
        except ValidationError as e:
            raise SkillManifestError(f"manifest schema 不符: {e.message} (path: {list(e.absolute_path)})") from e
        return SkillManifest(
            schema_version=int(data["schema_version"]),
            name=str(data["name"]),
            version=str(data.get("version", "0.1.0")),
            description=str(data["description"]),
            prompt=str(data["prompt"]),
            allowed_tools=list(data["allowed_tools"]) if data.get("allowed_tools") is not None else None,
            forbidden_tools=list(data.get("forbidden_tools", [])),
            tags=list(data.get("tags", [])),
            enabled=enabled,
        )

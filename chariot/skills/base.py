"""Skills ABC + manifest dataclass(B6 wave 1)。

封装策略(CLAUDE.md ⭐):
- 单一 BaseSkill ABC + frozen dataclass manifest;模块级零自由函数
- `prompt_block` / `tool_filter` 是实例方法(基于 manifest 派生),不是模块级 helper
- 两个子类(`BuiltinSkill` / `DbSkill`)只是 source 标签的差异,共用全部逻辑
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

SkillSource = Literal["builtin", "db"]


@dataclass(frozen=True)
class ToolFilter:
    """activator 用的工具过滤器。

    - `allowed` 为 None → 不过滤白名单(只看 forbidden)
    - `allowed` 为 list → 只保留命中的 tool name
    - `forbidden` 不管 allowed 是否命中,总是从结果里剔除
    """

    allowed: list[str] | None = None
    forbidden: list[str] = field(default_factory=list)

    def apply(self, tool_names: list[str]) -> list[str]:
        forbidden = set(self.forbidden)
        if self.allowed is None:
            return [t for t in tool_names if t not in forbidden]
        allowed = set(self.allowed)
        return [t for t in tool_names if t in allowed and t not in forbidden]


@dataclass(frozen=True)
class SkillManifest:
    """单个 skill 的 manifest 数据;YAML / DB row 解析后落到这里。

    跟 `chariot/skills/builtin/*.yaml` schema 1:1 对齐。
    """

    schema_version: int
    name: str
    version: str
    description: str
    prompt: str
    allowed_tools: list[str] | None = None
    forbidden_tools: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    enabled: bool = True


class BaseSkill(ABC):
    """单个 skill 的 Python 形态。

    子类来源两类:
    - `BuiltinSkill`:`builtin/*.yaml` 通过 `SkillLoader` 解析后构造,enabled 恒 True
    - `DbSkill`:`skills` 表行,enabled 由 `skills.enabled` 列决定

    两者共用 ABC,registry 不区分;只在 `source` property 区分。
    """

    @property
    @abstractmethod
    def manifest(self) -> SkillManifest: ...

    @property
    @abstractmethod
    def source(self) -> SkillSource: ...

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def enabled(self) -> bool:
        return self.manifest.enabled

    def prompt_block(self) -> str:
        """activator 用:把 manifest.prompt 包成 `<skill name="...">...</skill>` 块。

        约定:`</skill>` 标签让 LLM 容易识别 skill 区段的边界;name 字段供后续
        skill 切换 / 多 skill 叠加(未来扩展)时识别。
        """
        return f'<skill name="{self.name}">\n{self.manifest.prompt}\n</skill>'

    def tool_filter(self) -> ToolFilter:
        """activator 用:dispatch 层 / schema 层应用。"""
        return ToolFilter(
            allowed=list(self.manifest.allowed_tools) if self.manifest.allowed_tools is not None else None,
            forbidden=list(self.manifest.forbidden_tools),
        )


class BuiltinSkill(BaseSkill):
    """`chariot/skills/builtin/*.yaml` 加载后的具体 skill。"""

    def __init__(self, manifest: SkillManifest) -> None:
        self._manifest = manifest

    @property
    def manifest(self) -> SkillManifest:
        return self._manifest

    @property
    def source(self) -> SkillSource:
        return "builtin"


class DbSkill(BaseSkill):
    """`skills` 表行加载后的具体 skill。"""

    def __init__(self, manifest: SkillManifest) -> None:
        self._manifest = manifest

    @property
    def manifest(self) -> SkillManifest:
        return self._manifest

    @property
    def source(self) -> SkillSource:
        return "db"

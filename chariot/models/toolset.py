"""Toolset 领域数据形态。

`Toolset` 是 0.7.2-tool 新引入的命名实体:把一组 tool name 收集成可命名 /
可绑定的集合,作为 `agent_profile.tool_profile` 字段的引用目标。

设计:docs/tool-profile-design.md。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Toolset:
    """命名 toolset(`toolsets` 表)。"""

    name: str
    description: str | None
    id: str = ""
    members: tuple[str, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class ToolsetMember:
    """toolset 的一个成员(`toolset_members` 表的行)。"""

    toolset_name: str
    tool_name: str
    toolset_id: str | None = None

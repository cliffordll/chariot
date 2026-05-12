"""Tool 领域数据形态。

`ToolEntry` 是 `tools` 表的业务对象,跟 SQLAlchemy `ToolRow` 解耦。0.7.2 起
从 `chariot/agent/config.py` 迁来 —— 跟 `ProviderEntry` 同一处理,把领域
数据 dataclass 集中到 `chariot/models/`。

`ToolConfig`(顶层 enabled-tools 容器)留在 `chariot/agent/config.py`,跟
`ChariotConfig` 一起作为 agent 启动期的配置形态。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class ToolEntry:
    """单条 tool 配置(0.8.7)。

    `name` == ToolRegistry type key(0.4.0 一种 type 一个 entry,预留同类多实例
    时再分;详见 `docs/DESIGN.md` §8)。`options` 形态依 type 而定:

    - `read_file`:`{"max_bytes": int}`
    - `list_dir`:`{}`
    - `shell_exec`:`{"workdir": str, "timeout_s": int}`
    - `http_get`:`{"allowed_domains": list[str], "max_bytes": int}`
    - `http_custom`:`{"method": str, "url": str, "headers": dict, ...}`
    - `shell_custom`:`{"command": str, "workdir": str, "timeout_s": int}`

    0.8.7 新增 `source` / `description` / `custom_type` 字段,支持自定义工具。
    """

    name: str
    type: str
    enabled: bool
    options: dict[str, Any]
    source: Literal["builtin", "custom"] = "builtin"
    description: str = ""
    custom_type: str | None = None

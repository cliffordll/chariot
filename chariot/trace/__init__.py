"""Trace platform(Phase B1)。

`TraceWriter` 是 AIAgent / AgentLoop 的唯一 trace 写入入口,best-effort 失败
不阻断主链路。每次 chat turn 通过 `writer.begin_turn(...)` 拿 `TurnHandle`,
子事件用 `turn.begin_provider_call(...) / begin_tool_call(...)` 配对 finish。

设计文档:docs/evolution-design.md §4。
"""

from __future__ import annotations

from chariot.trace.writer import (
    ProviderCallHandle,
    ToolCallHandle,
    TraceWriter,
    TurnHandle,
)

__all__ = [
    "ProviderCallHandle",
    "ToolCallHandle",
    "TraceWriter",
    "TurnHandle",
]

"""TraceCostExtractor —— 从 B1 trace 表反查 token / cost / tool_calls,填进 RunRecord。

为什么不让 Runner 自己拼:trace 表的字段细节(`turns = len(provider_calls)` 这种)
跟 B1 schema 紧耦合,集中在一个 helper 类里方便后续 B1 schema 演进时只改一处。
Runner 只需要拿到 turn_id 就能调 populate,关注点分离。
"""

from __future__ import annotations

from chariot.models.eval import RunRecord
from chariot.repos.trace_repo import TraceRepo


class TraceCostExtractor:
    """从 trace_turns + trace_provider_calls + trace_tool_calls 拼 RunRecord 的 cost/tools 字段。"""

    def __init__(self, repo: TraceRepo) -> None:
        self._repo = repo

    async def populate(self, record: RunRecord, turn_id: str) -> None:
        """填 record.turn_id / turns / tokens / cost / duration / tool_calls。

        Turn 不存在 → 静默退出(record 保留默认值);eval 仍能跑,但 cost 数据为 0。
        填进 record.tool_calls 的格式跟 ToolCalledVerifier 期望对齐(tool_name + arguments)。
        """
        turn = await self._repo.get_turn(turn_id)
        if turn is None:
            return
        record.turn_id = turn_id
        record.input_tokens = turn.input_tokens or 0
        record.output_tokens = turn.output_tokens or 0
        record.cost_usd = turn.cost_usd or 0.0
        record.cost_status = turn.cost_status or "unknown"
        record.duration_seconds = (turn.duration_ms or 0) / 1000.0

        provider_calls = await self._repo.list_provider_calls(turn_id)
        record.turns = len(provider_calls)

        tool_call_rows = await self._repo.list_tool_calls(turn_id)
        record.tool_calls = [
            {
                "tool_name": tc.tool_name,
                "arguments": tc.arguments,
                "status": tc.status.value,
                "result_summary": tc.result_summary,
                "duration_ms": tc.duration_ms,
                "error_message": tc.error_message,
            }
            for tc in tool_call_rows
        ]

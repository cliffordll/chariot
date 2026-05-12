"""TrajectoryExporter —— 从 chariot DB 拼出一条 conversation 的完整 trajectory(B7 wave 1)。

封装策略(CLAUDE.md ⭐):
- 单类编排;模块级零自由函数
- 接 `sessionmaker` + `scrubber`(默认 `SecretScrubber`,可注入 `NullScrubber` 走 raw)
- 一条 conversation → 多条 `ExportEntry`(每个 trace_turn 一条)
- 落 disk **不** 进 sqlite(JSONL 文件,路径由 caller 决定)

数据拼装:
- 主表:`trace_turns` filter by conversation_id,按 started_at asc 排
- 关联 `messages`(同 conversation,按 seq asc;按 turn 时间窗切分给 prompt/response)
- 关联 `trace_provider_calls`(每 turn 取最近一条,extract usage / latency)
- 关联 `trace_tool_calls`(每 turn 多条,extract name / args / result_summary)
- 关联 `audit_events`(按 created_at 在 turn 窗口内,分桶 guardrail / skill / reflection)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chariot.audit.hooks import AuditHookManager
from chariot.rl.base import ExportEntry
from chariot.rl.scrubber import SecretScrubber

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class TrajectoryExporter:
    """conversation_id → list[ExportEntry] / JSONL 文件。

    用法::

        exporter = TrajectoryExporter(sessionmaker=sm, scrubber=SecretScrubber())
        entries = await exporter.export("cv-abc")
        # 或直接落盘
        rows = await exporter.export_to_jsonl("cv-abc", Path("/tmp/out.jsonl"))
    """

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        scrubber: SecretScrubber | None = None,
        audit_hooks: AuditHookManager | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._scrubber = scrubber or SecretScrubber()
        self._audit_hooks = audit_hooks or AuditHookManager(None)

    async def export(self, conversation_id: str) -> list[ExportEntry]:
        """主入口。读 DB 拼 entries,按 turn started_at 升序返。"""
        from chariot.models.trace import TurnStatus  # noqa: F401 — 类型对齐用
        from chariot.repos.audit_repo import AuditRepo
        from chariot.repos.conversation_repo import ConversationRepo
        from chariot.repos.trace_repo import TraceRepo

        async with self._sessionmaker() as session:
            turns = await TraceRepo(session).list_turns(
                conversation_id=conversation_id,
                limit=10000,  # conversation 极少超过 10k turn;到这量级再说
            )
            # list_turns 默认 started_at desc + id desc;reverse 成 asc
            turns_asc = sorted(turns, key=lambda t: (t.started_at, t.id))
            if not turns_asc:
                return []
            # 一次性把同 conversation 的 messages / audit 读出来(避免 N+1)
            messages = await ConversationRepo(session).list_messages(conversation_id)
            audit_events_raw = await AuditRepo(session).list_events(limit=10000)
            audit_events = [
                ev for ev in audit_events_raw if self._audit_belongs_to_conv(ev, conversation_id, turns_asc)
            ]
            # 每个 turn 也单独读 provider_calls / tool_calls
            entries: list[ExportEntry] = []
            for idx, turn in enumerate(turns_asc):
                provider_calls = await TraceRepo(session).list_provider_calls(turn.id)
                tool_calls = await TraceRepo(session).list_tool_calls(turn.id)
                window_start = turn.started_at
                window_end = turn.finished_at or self._next_turn_started(turns_asc, idx)
                entry = self._build_entry(
                    turn=turn,
                    sequence=idx,
                    messages_rows=messages,
                    provider_calls=provider_calls,
                    tool_calls=tool_calls,
                    audit_events=audit_events,
                    window_start=window_start,
                    window_end=window_end,
                )
                entries.append(entry)
        return entries

    async def export_to_jsonl(self, conversation_id: str, out: Path) -> int:
        """写 JSONL 文件,返行数。父目录不存在自动 mkdir。"""
        entries = await self.export(conversation_id)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry.to_dict(), ensure_ascii=False, default=str))
                f.write("\n")
        # B7 wave 1:audit 写一条 rl_export 事件(payload 含路径 / 行数 / scrub 模式)
        await self._audit_hooks.record_rl_export(
            conversation_id=conversation_id,
            out_path=str(out),
            row_count=len(entries),
            scrub_mode=self._scrub_mode(),
        )
        return len(entries)

    # ---- 内部:拼装 ----

    def _build_entry(
        self,
        *,
        turn: Any,
        sequence: int,
        messages_rows: list[Any],
        provider_calls: list[Any],
        tool_calls: list[Any],
        audit_events: list[Any],
        window_start: datetime,
        window_end: datetime | None,
    ) -> ExportEntry:
        # 1. prompt / response —— 把 messages 按 turn 时间窗切分
        #    prompt = 这个 turn 之前 + 本 turn 内的 user 消息
        #    response = 本 turn 内的 assistant 消息
        prompt_blocks: list[dict[str, Any]] = []
        response_blocks: list[dict[str, Any]] = []
        for msg in messages_rows:
            content = self._deserialize_content(msg.content)
            content_list = content if isinstance(content, list) else [{"type": "text", "text": str(content)}]
            scrubbed = self._scrubber.scrub_blocks(content_list)
            if msg.created_at < window_start:
                prompt_blocks.extend(scrubbed)
            elif window_end is None or msg.created_at <= window_end:
                if msg.role == "user":
                    prompt_blocks.extend(scrubbed)
                else:
                    response_blocks.extend(scrubbed)

        # 2. tool_calls
        tool_calls_dicts = [
            {
                "name": tc.tool_name,
                "args": self._scrubber.scrub_value(tc.arguments),
                "result_summary": self._scrubber.scrub_value(tc.result_summary) if tc.result_summary else None,
                "status": tc.status.value if hasattr(tc.status, "value") else str(tc.status),
                "duration_ms": tc.duration_ms,
                "is_error": (tc.status.value if hasattr(tc.status, "value") else str(tc.status)) == "error",
            }
            for tc in tool_calls
        ]

        # 3. provider_call(取第一条;rare 多条时仅记 latest)
        pc = provider_calls[-1] if provider_calls else None
        provider_call_dict: dict[str, Any] = {
            "provider": pc.provider_name if pc else turn.provider_name,
            "model": pc.model if pc else turn.model,
            "latency_ms": pc.latency_ms if pc else None,
            "usage": {
                "input_tokens": turn.input_tokens,
                "output_tokens": turn.output_tokens,
                "cache_read_tokens": turn.cache_read_tokens,
                "cache_write_tokens": turn.cache_write_tokens,
            },
        }

        # 4. audit_signals —— 在 turn 窗口内的 audit_events 分桶
        guardrail_hits: list[dict[str, Any]] = []
        skill_activations: list[dict[str, Any]] = []
        reflection_records: list[dict[str, Any]] = []
        memory_stores: list[dict[str, Any]] = []
        for ev in audit_events:
            if ev.created_at < window_start:
                continue
            if window_end is not None and ev.created_at > window_end:
                continue
            if ev.event_type == AuditHookManager.EVENT_GUARDRAIL_VERDICT:
                guardrail_hits.append({"rule_id": ev.payload.get("rule_id"), "verdict": ev.payload.get("verdict")})
            elif ev.event_type == AuditHookManager.EVENT_SKILL_ACTIVATE:
                skill_activations.append(
                    {
                        "skill_name": ev.payload.get("skill_name"), 
                        "status": ev.status, 
                        "is_error": ev.payload.get("is_error")
                    }
                )
            elif ev.event_type == AuditHookManager.EVENT_MEMORY_STORE:
                memory_stores.append({"action": ev.payload.get("action"), "kind": ev.payload.get("kind")})
            # reflection 暂没专属 event_type;通过 turn.meta 拿(见下)

        # 从 turn.meta 提 reflection 信号(B4 wave 2 把 verdict 写进 meta)
        meta = turn.meta if isinstance(turn.meta, dict) else {}
        reflection_verdict = meta.get("reflection_previous_verdict")
        if reflection_verdict:
            reflection_records.append({"verdict": reflection_verdict, "reason": meta.get("reflection_previous_reason")})

        audit_signals = {
            "guardrail_hits": guardrail_hits,
            "skill_activations": skill_activations,
            "reflection_records": reflection_records,
            "memory_stores": memory_stores,
        }

        return ExportEntry(
            conversation_id=str(turn.conversation_id),
            turn_id=turn.id,
            sequence=sequence,
            prompt=prompt_blocks,
            response=response_blocks,
            tool_calls=tool_calls_dicts,
            provider_call=provider_call_dict,
            audit_signals=audit_signals,
            reward=None,
            reward_breakdown=None,
            agent_profile=turn.agent_profile,
            stop_reason=turn.stop_reason,
            error_type=turn.error_type,
            started_at=turn.started_at.isoformat(),
            duration_ms=turn.duration_ms,
        )

    # ---- helpers ----

    @staticmethod
    def _deserialize_content(raw: Any) -> Any:
        """messages.content 在 DB 里是 JSON 文本;解出 list / str。"""
        if isinstance(raw, (list, dict)):
            return raw
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return raw
        return raw

    @staticmethod
    def _next_turn_started(turns: list[Any], idx: int) -> datetime | None:
        if idx + 1 < len(turns):
            return turns[idx + 1].started_at  # type: ignore[no-any-return]
        return None

    @staticmethod
    def _audit_belongs_to_conv(
        ev: Any,
        conversation_id: str,
        turns_asc: list[Any],
    ) -> bool:
        """audit_events 没 conversation_id 列;按时间窗判定:落在该 conv 任一
        turn 的 [started_at, finished_at] 区间。

        粗筛 —— 时间重叠 + payload 中带 conversation_id 字段(memory_store /
        skill_activate 写时填了)。"""
        payload_conv = ev.payload.get("conversation_id") if isinstance(ev.payload, dict) else None
        if isinstance(payload_conv, str) and payload_conv == conversation_id:
            return True
        # 按时间窗:落进任一 turn 的执行区间
        for turn in turns_asc:
            start = turn.started_at
            end = turn.finished_at
            if end is None:
                continue
            if start <= ev.created_at <= end:
                return True
        return False

    def _scrub_mode(self) -> str:
        from chariot.rl.scrubber import NullScrubber

        return "raw" if isinstance(self._scrubber, NullScrubber) else "default"

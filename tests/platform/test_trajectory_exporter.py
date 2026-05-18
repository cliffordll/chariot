"""B7 wave 1 —— TrajectoryExporter 从 DB 拼 ExportEntry + JSONL 落盘。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chariot.audit.hooks import AuditHookManager
from chariot.database.session import dispose_db, init_db
from chariot.models.trace import ToolCallStatus, TurnStatus
from chariot.repos.audit_repo import AuditRepo
from chariot.repos.conversation_repo import ConversationRepo
from chariot.repos.trace_repo import TraceRepo
from chariot.rl import NullScrubber, TrajectoryExporter


@pytest_asyncio.fixture
async def sm(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    sm = await init_db(tmp_path / "trajectory.db")
    try:
        yield sm
    finally:
        await dispose_db()


async def _seed_conversation(
    sm: async_sessionmaker[AsyncSession],
    conv_id: str = "cv-1",
    *,
    with_tool_call: bool = False,
    with_guardrail: bool = False,
) -> str:
    """落最小 conversation:1 个 turn,user + assistant 消息,可选 tool_call + audit。"""
    async with sm() as session:
        # conversation + messages
        repo = ConversationRepo(session)
        await repo.ensure_exists(conv_id)
        await repo.append_message(conv_id, role="user", content=[{"type": "text", "text": "hello"}])

        # trace turn
        turn_repo = TraceRepo(session)
        turn = await turn_repo.create_turn(
            conversation_id=conv_id,
            provider_snapshot="mock",
            model="mock-1",
            agent_profile=None,
        )
        # provider call
        pc = await turn_repo.record_provider_call(turn_id=turn.id, provider_snapshot="mock", model="mock-1")
        await turn_repo.finalize_provider_call(
            pc.id,
            response_summary={"stop_reason": "end_turn", "usage": {"input_tokens": 10, "output_tokens": 5}},
        )
        # tool call (optional)
        if with_tool_call:
            tc = await turn_repo.record_tool_call(
                turn_id=turn.id, provider_call_id=pc.id, tool_name="list_dir", arguments={"path": "."}
            )
            await turn_repo.finalize_tool_call(
                tc.id,
                status=ToolCallStatus.OK,
                result_summary={"snippet": "files"},
            )
        # assistant 消息(window 内)
        await repo.append_message(conv_id, role="assistant", content=[{"type": "text", "text": "hi back"}])
        # audit (optional)
        if with_guardrail:
            hooks = AuditHookManager(sm)
            await hooks.record_guardrail_verdict(
                rule_id="shell_rm_rf",
                verdict="deny",
                tool_name="shell_exec",
                matched_pattern="rm -rf",
                quota_remaining=None,
                quota_exhausted=False,
            )
        # turn finalize
        await turn_repo.finalize_turn(
            turn.id,
            status=TurnStatus.COMPLETED,
            stop_reason="end_turn",
            input_tokens=10,
            output_tokens=5,
        )
        return turn.id


# ---- export ----


async def test_export_empty_conversation_returns_empty(sm: async_sessionmaker[AsyncSession], tmp_path: Path) -> None:
    exporter = TrajectoryExporter(sessionmaker=sm)
    out = await exporter.export("does-not-exist")
    assert out == []


async def test_export_single_turn(sm: async_sessionmaker[AsyncSession]) -> None:
    turn_id = await _seed_conversation(sm)
    exporter = TrajectoryExporter(sessionmaker=sm, scrubber=NullScrubber())
    entries = await exporter.export("cv-1")
    assert len(entries) == 1
    e = entries[0]
    assert e.conversation_id == "cv-1"
    assert e.turn_id == turn_id
    assert e.sequence == 0
    assert e.stop_reason == "end_turn"
    # prompt 含 user "hello",response 含 assistant "hi back"
    prompt_text = " ".join(b.get("text", "") for b in e.prompt if isinstance(b, dict))
    response_text = " ".join(b.get("text", "") for b in e.response if isinstance(b, dict))
    assert "hello" in prompt_text
    assert "hi back" in response_text
    # usage 透传
    assert e.provider_call["usage"]["input_tokens"] == 10
    assert e.provider_call["usage"]["output_tokens"] == 5


async def test_export_with_tool_call(sm: async_sessionmaker[AsyncSession]) -> None:
    await _seed_conversation(sm, with_tool_call=True)
    exporter = TrajectoryExporter(sessionmaker=sm)
    entries = await exporter.export("cv-1")
    assert len(entries) == 1
    e = entries[0]
    assert len(e.tool_calls) == 1
    tc = e.tool_calls[0]
    assert tc["name"] == "list_dir"
    assert tc["args"] == {"path": "."}
    assert tc["status"] == "ok"
    assert tc["is_error"] is False


async def test_export_with_guardrail_audit(sm: async_sessionmaker[AsyncSession]) -> None:
    """audit_events.guardrail_verdict 在 turn 窗口内 → audit_signals.guardrail_hits 非空。"""
    await _seed_conversation(sm, with_guardrail=True)
    exporter = TrajectoryExporter(sessionmaker=sm)
    entries = await exporter.export("cv-1")
    assert len(entries) == 1
    hits = entries[0].audit_signals["guardrail_hits"]
    assert len(hits) >= 1
    assert hits[0]["rule_id"] == "shell_rm_rf"


# ---- export_to_jsonl 落盘 ----


async def test_export_to_jsonl_writes_file(sm: async_sessionmaker[AsyncSession], tmp_path: Path) -> None:
    await _seed_conversation(sm)
    out = tmp_path / "traj" / "out.jsonl"  # 父目录不存在
    exporter = TrajectoryExporter(sessionmaker=sm)
    row_count = await exporter.export_to_jsonl("cv-1", out)
    assert row_count == 1
    assert out.exists()
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["conversation_id"] == "cv-1"
    assert parsed["sequence"] == 0


async def test_export_to_jsonl_writes_audit_event(sm: async_sessionmaker[AsyncSession], tmp_path: Path) -> None:
    await _seed_conversation(sm)
    out = tmp_path / "out.jsonl"
    hooks = AuditHookManager(sm)
    exporter = TrajectoryExporter(sessionmaker=sm, audit_hooks=hooks)
    await exporter.export_to_jsonl("cv-1", out)
    async with sm() as session:
        events = await AuditRepo(session).list_events(limit=10)
    rl_events = [ev for ev in events if ev.event_type == AuditHookManager.EVENT_RL_EXPORT]
    assert len(rl_events) == 1
    payload = rl_events[0].payload
    assert payload["conversation_id"] == "cv-1"
    assert payload["row_count"] == 1
    assert payload["scrub_mode"] == "default"


async def test_export_with_secrets_default_scrubs(sm: async_sessionmaker[AsyncSession], tmp_path: Path) -> None:
    """messages 含 secret → 默认 SecretScrubber 替换。"""
    async with sm() as session:
        repo = ConversationRepo(session)
        await repo.ensure_exists("cv-sec")
        await repo.append_message(
            "cv-sec",
            role="user",
            content=[{"type": "text", "text": "my key is sk-abcdef1234567890abcdef"}],
        )
        turn_repo = TraceRepo(session)
        turn = await turn_repo.create_turn(conversation_id="cv-sec", provider_snapshot="mock")
        await repo.append_message("cv-sec", role="assistant", content=[{"type": "text", "text": "ok"}])
        await turn_repo.finalize_turn(turn.id, status=TurnStatus.COMPLETED)
    out = tmp_path / "sec.jsonl"
    exporter = TrajectoryExporter(sessionmaker=sm)  # 默认 SecretScrubber
    await exporter.export_to_jsonl("cv-sec", out)
    content = out.read_text(encoding="utf-8")
    assert "sk-abcdef" not in content
    assert "REDACTED" in content


async def test_export_with_raw_scrubber_preserves_secrets(sm: async_sessionmaker[AsyncSession], tmp_path: Path) -> None:
    """--raw / NullScrubber 模式:secret 透传。"""
    async with sm() as session:
        repo = ConversationRepo(session)
        await repo.ensure_exists("cv-raw")
        await repo.append_message(
            "cv-raw",
            role="user",
            content=[{"type": "text", "text": "raw=sk-abcdef1234567890abcdef"}],
        )
        turn_repo = TraceRepo(session)
        turn = await turn_repo.create_turn(conversation_id="cv-raw", provider_snapshot="mock")
        await repo.append_message("cv-raw", role="assistant", content=[{"type": "text", "text": "ok"}])
        await turn_repo.finalize_turn(turn.id, status=TurnStatus.COMPLETED)
    out = tmp_path / "raw.jsonl"
    exporter = TrajectoryExporter(sessionmaker=sm, scrubber=NullScrubber())
    await exporter.export_to_jsonl("cv-raw", out)
    content = out.read_text(encoding="utf-8")
    assert "sk-abcdef" in content

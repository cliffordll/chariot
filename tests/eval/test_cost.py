"""TraceCostExtractor —— 用真实 TraceRepo + tmp DB 验证 populate 行为。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest_asyncio

from chariot.database.session import dispose_db, init_db
from chariot.eval.cost import TraceCostExtractor
from chariot.models.eval import RunRecord, Verdict
from chariot.models.trace import ToolCallStatus, TurnStatus
from chariot.repos.trace_repo import TraceRepo


@pytest_asyncio.fixture
async def session_maker(tmp_path: Path):
    sm = await init_db(tmp_path / "test.db")
    try:
        yield sm
    finally:
        await dispose_db()


async def _seed_turn(repo: TraceRepo, *, provider: str = "mock", model: str = "mock-1") -> str:
    turn = await repo.create_turn(provider_name=provider, model=model)
    await repo.finalize_turn(
        turn.id,
        status=TurnStatus.COMPLETED,
        stop_reason="end_turn",
        input_tokens=120,
        output_tokens=80,
        cost_usd=0.0042,
        cost_status="estimated",
        duration_ms=3500,
    )
    return turn.id


async def test_populate_fills_from_trace(session_maker) -> None:
    async with session_maker() as session:
        repo = TraceRepo(session)
        turn_id = await _seed_turn(repo)
        await repo.record_provider_call(
            turn_id=turn_id,
            provider_name="mock",
            model="mock-1",
            request_summary={"message_count": 1},
            response_summary={"stop_reason": "end_turn"},
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            latency_ms=1000,
        )
        await repo.record_tool_call(
            turn_id=turn_id,
            tool_name="read_file",
            arguments={"path": "README.md"},
            result_summary={"is_error": False, "snippet": "..."},
            duration_ms=12,
            status=ToolCallStatus.OK,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        record = RunRecord(task_id="t", verdict=Verdict.PASS)
        await TraceCostExtractor(repo).populate(record, turn_id)
    assert record.turn_id == turn_id
    assert record.input_tokens == 120
    assert record.output_tokens == 80
    assert record.cost_usd == 0.0042
    assert record.cost_status == "estimated"
    assert record.duration_seconds == 3.5
    assert record.turns == 1
    assert len(record.tool_calls) == 1
    assert record.tool_calls[0]["tool_name"] == "read_file"
    assert record.tool_calls[0]["arguments"] == {"path": "README.md"}
    assert record.tool_calls[0]["status"] == "ok"


async def test_populate_no_turn_silent(session_maker) -> None:
    """trace_turns 不存在 turn_id → 静默退出,record 保留默认值。"""
    async with session_maker() as session:
        record = RunRecord(task_id="t", verdict=Verdict.PASS)
        await TraceCostExtractor(TraceRepo(session)).populate(record, "ghost_turn_id")
    assert record.turn_id == ""
    assert record.input_tokens == 0
    assert record.cost_usd == 0.0
    assert record.tool_calls == []


async def test_populate_multiple_provider_calls_counts_turns(session_maker) -> None:
    """工具循环跑 2 轮 → record.turns == 2。"""
    async with session_maker() as session:
        repo = TraceRepo(session)
        turn_id = await _seed_turn(repo)
        for _ in range(2):
            await repo.record_provider_call(
                turn_id=turn_id,
                provider_name="mock",
                model="mock-1",
                request_summary={},
                response_summary={},
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
            )
        record = RunRecord(task_id="t", verdict=Verdict.PASS)
        await TraceCostExtractor(repo).populate(record, turn_id)
    assert record.turns == 2

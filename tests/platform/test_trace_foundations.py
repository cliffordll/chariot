"""TraceRepo / TraceService unit tests(v17 Phase B1)。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.exceptions import ConfigError
from chariot.database.session import dispose_db, init_db
from chariot.models.trace import CheckpointKind, ToolCallStatus, TurnStatus
from chariot.repos.trace_repo import TraceRepo
from chariot.services.trace import TraceService


@pytest_asyncio.fixture
async def session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    db_path = tmp_path / "chariot.db"
    sm = await init_db(db_path)
    async with sm() as s:
        yield s
    await dispose_db()


class TestTraceRepoTurnLifecycle:
    async def test_create_and_get(self, session: AsyncSession) -> None:
        repo = TraceRepo(session)
        turn = await repo.create_turn(provider_name="mock", conversation_id="conv-1")
        assert turn.status == TurnStatus.RUNNING
        assert turn.provider_name == "mock"
        assert turn.conversation_id == "conv-1"

        loaded = await repo.get_turn(turn.id)
        assert loaded is not None
        assert loaded.id == turn.id

    async def test_finalize_completed(self, session: AsyncSession) -> None:
        repo = TraceRepo(session)
        turn = await repo.create_turn(provider_name="mock")
        final = await repo.finalize_turn(
            turn.id,
            status=TurnStatus.COMPLETED,
            stop_reason="end_turn",
            input_tokens=120,
            output_tokens=45,
            cost_usd=0.0012,
            cost_status="estimated",
            duration_ms=850,
        )
        assert final.status == TurnStatus.COMPLETED
        assert final.input_tokens == 120
        assert final.cost_usd == pytest.approx(0.0012)
        assert final.finished_at is not None

    async def test_finalize_failed(self, session: AsyncSession) -> None:
        repo = TraceRepo(session)
        turn = await repo.create_turn(provider_name="mock")
        final = await repo.finalize_turn(
            turn.id,
            status=TurnStatus.FAILED,
            error_type="upstream_server_error",
            error_message="500",
        )
        assert final.status == TurnStatus.FAILED
        assert final.error_type == "upstream_server_error"

    async def test_finalize_missing_raises(self, session: AsyncSession) -> None:
        repo = TraceRepo(session)
        with pytest.raises(ConfigError):
            await repo.finalize_turn("ghost", status=TurnStatus.COMPLETED)


class TestTraceRepoChildEvents:
    async def test_record_provider_call(self, session: AsyncSession) -> None:
        repo = TraceRepo(session)
        turn = await repo.create_turn(provider_name="mock")
        call = await repo.record_provider_call(
            turn.id,
            provider_name="mock",
            model="mock-1",
            request_summary={"message_count": 3, "tool_count": 2},
            response_summary={"stop_reason": "end_turn", "output_tokens": 30},
            latency_ms=420,
        )
        assert call.turn_id == turn.id
        assert call.request_summary["message_count"] == 3
        assert call.response_summary["stop_reason"] == "end_turn"
        assert call.latency_ms == 420

    async def test_record_tool_call_ok_and_error(self, session: AsyncSession) -> None:
        repo = TraceRepo(session)
        turn = await repo.create_turn(provider_name="mock")
        ok = await repo.record_tool_call(
            turn.id,
            tool_name="read_file",
            arguments={"path": "README.md"},
            result_summary={"is_error": False, "snippet": "hello"},
            status=ToolCallStatus.OK,
            duration_ms=18,
        )
        assert ok.status == ToolCallStatus.OK
        assert ok.arguments["path"] == "README.md"

        err = await repo.record_tool_call(
            turn.id,
            tool_name="shell_exec",
            arguments={"command": "ls /tmp"},
            status=ToolCallStatus.ERROR,
            error_message="permission denied",
        )
        assert err.status == ToolCallStatus.ERROR
        assert err.error_message == "permission denied"
        assert err.result_summary is None

    async def test_record_checkpoint(self, session: AsyncSession) -> None:
        repo = TraceRepo(session)
        turn = await repo.create_turn(provider_name="mock")
        cp = await repo.record_checkpoint(turn.id, kind=CheckpointKind.BEFORE_TOOL)
        assert cp.kind == CheckpointKind.BEFORE_TOOL


class TestTraceRepoQuery:
    async def test_list_filters_and_order(self, session: AsyncSession) -> None:
        repo = TraceRepo(session)
        t1 = await repo.create_turn(provider_name="mock", conversation_id="A")
        t2 = await repo.create_turn(provider_name="mock", conversation_id="B")
        t3 = await repo.create_turn(provider_name="alt", conversation_id="A")
        await repo.finalize_turn(t2.id, status=TurnStatus.COMPLETED)

        # 全部:按 started_at desc(后插入的先返)
        all_turns = await repo.list_turns()
        assert [t.id for t in all_turns][:3] == [t3.id, t2.id, t1.id]

        # 按 conversation
        a_turns = await repo.list_turns(conversation_id="A")
        assert {t.id for t in a_turns} == {t1.id, t3.id}

        # 按 provider
        mock_turns = await repo.list_turns(provider_name="mock")
        assert {t.id for t in mock_turns} == {t1.id, t2.id}

        # 按 status
        completed = await repo.list_turns(status=TurnStatus.COMPLETED)
        assert [t.id for t in completed] == [t2.id]

    async def test_get_tree(self, session: AsyncSession) -> None:
        repo = TraceRepo(session)
        turn = await repo.create_turn(provider_name="mock")
        await repo.record_provider_call(turn.id, provider_name="mock", latency_ms=100)
        await repo.record_tool_call(turn.id, tool_name="read_file", status=ToolCallStatus.OK)
        await repo.record_tool_call(turn.id, tool_name="list_dir", status=ToolCallStatus.OK)
        await repo.record_checkpoint(turn.id, kind=CheckpointKind.BEFORE_APPLY)

        tree = await repo.get_tree(turn.id)
        assert tree is not None
        assert len(tree.provider_calls) == 1
        assert len(tree.tool_calls) == 2
        assert len(tree.checkpoints) == 1
        assert {t.tool_name for t in tree.tool_calls} == {"read_file", "list_dir"}

    async def test_get_tree_missing(self, session: AsyncSession) -> None:
        assert await TraceRepo(session).get_tree("ghost") is None


class TestTraceRepoReconcile:
    async def test_reconcile_old_running_turns(self, session: AsyncSession) -> None:
        repo = TraceRepo(session)
        t = await repo.create_turn(provider_name="mock")
        # 直接改 started_at 到很远的过去模拟 stale running
        from datetime import UTC, datetime, timedelta

        from chariot.database.models import TraceTurnRow

        row = await session.get(TraceTurnRow, t.id)
        assert row is not None
        row.started_at = datetime.now(UTC) - timedelta(hours=2)
        await session.commit()

        cleaned = await repo.reconcile_stale(older_than_seconds=3600)
        assert cleaned == 1
        loaded = await repo.get_turn(t.id)
        assert loaded is not None
        assert loaded.status == TurnStatus.CANCELLED
        assert loaded.error_type == "stale_running"


class TestTraceService:
    async def test_service_wraps_repo(self, session: AsyncSession) -> None:
        service = TraceService(TraceRepo(session))
        turn = await service.create_turn(provider_name="mock")
        await service.record_provider_call(turn.id, provider_name="mock", latency_ms=200)
        await service.record_tool_call(turn.id, tool_name="read_file", status=ToolCallStatus.OK)
        await service.finalize_turn(
            turn.id,
            status=TurnStatus.COMPLETED,
            stop_reason="end_turn",
            input_tokens=10,
            output_tokens=5,
        )

        listed = await service.list_turns()
        assert len(listed) == 1
        tree = await service.get_tree(turn.id)
        assert tree is not None
        assert tree.turn.status == TurnStatus.COMPLETED
        assert len(tree.tool_calls) == 1

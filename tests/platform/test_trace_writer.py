"""TraceWriter 测试(Phase B1 phase 2)。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chariot.database.session import dispose_db, init_db
from chariot.models.trace import CheckpointKind, ToolCallStatus, TurnStatus
from chariot.repos.trace_repo import TraceRepo
from chariot.trace import TraceWriter


@pytest_asyncio.fixture
async def sessionmaker(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    db_path = tmp_path / "chariot.db"
    sm = await init_db(db_path)
    try:
        yield sm
    finally:
        await dispose_db()


class TestTraceWriterHappyPath:
    async def test_turn_provider_tool_checkpoint_pairing(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
    ) -> None:
        writer = TraceWriter(sessionmaker)
        turn = await writer.begin_turn(provider_name="mock", conversation_id="C1")
        assert turn.turn_id is not None

        pc = turn.begin_provider_call(provider_name="mock", model="mock-1")
        await pc.finish(response_summary={"stop_reason": "end_turn", "output_tokens": 5})

        tc = turn.begin_tool_call(tool_name="read_file", arguments={"path": "README.md"})
        await tc.finish(status=ToolCallStatus.OK, result_summary={"snippet": "hi"})

        await turn.record_checkpoint(kind=CheckpointKind.BEFORE_APPLY)
        await turn.finalize(
            status=TurnStatus.COMPLETED,
            stop_reason="end_turn",
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.0001,
            cost_status="estimated",
        )

        async with sessionmaker() as session:
            repo = TraceRepo(session)
            tree = await repo.get_tree(turn.turn_id)
            assert tree is not None
            assert tree.turn.status == TurnStatus.COMPLETED
            assert tree.turn.input_tokens == 10
            assert tree.turn.duration_ms is not None
            assert len(tree.provider_calls) == 1
            assert tree.provider_calls[0].response_summary["stop_reason"] == "end_turn"
            assert tree.provider_calls[0].latency_ms is not None
            assert len(tree.tool_calls) == 1
            assert tree.tool_calls[0].status == ToolCallStatus.OK
            assert tree.tool_calls[0].duration_ms is not None
            assert len(tree.checkpoints) == 1
            assert tree.checkpoints[0].kind == CheckpointKind.BEFORE_APPLY

    async def test_provider_call_records_error(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
    ) -> None:
        writer = TraceWriter(sessionmaker)
        turn = await writer.begin_turn(provider_name="mock")
        pc = turn.begin_provider_call(provider_name="mock")
        await pc.finish(error_type="upstream_server_error")
        await turn.finalize(status=TurnStatus.FAILED, error_type="upstream_server_error")

        async with sessionmaker() as session:
            tree = await TraceRepo(session).get_tree(turn.turn_id or "")
            assert tree is not None
            assert tree.provider_calls[0].error_type == "upstream_server_error"
            assert tree.turn.status == TurnStatus.FAILED

    async def test_tool_call_records_error(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
    ) -> None:
        writer = TraceWriter(sessionmaker)
        turn = await writer.begin_turn(provider_name="mock")
        tc = turn.begin_tool_call(tool_name="shell_exec", arguments={"command": "ls /tmp"})
        await tc.finish(status=ToolCallStatus.ERROR, error_message="permission denied")
        await turn.finalize(status=TurnStatus.COMPLETED)

        async with sessionmaker() as session:
            tree = await TraceRepo(session).get_tree(turn.turn_id or "")
            assert tree is not None
            assert tree.tool_calls[0].status == ToolCallStatus.ERROR
            assert tree.tool_calls[0].error_message == "permission denied"


class TestTraceWriterIdempotence:
    async def test_double_finalize_is_noop(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
    ) -> None:
        writer = TraceWriter(sessionmaker)
        turn = await writer.begin_turn(provider_name="mock")
        await turn.finalize(status=TurnStatus.COMPLETED, stop_reason="end_turn")
        # 第二次 finalize 直接 no-op,不抛
        await turn.finalize(status=TurnStatus.FAILED, error_type="oops")

        async with sessionmaker() as session:
            loaded = await TraceRepo(session).get_turn(turn.turn_id or "")
            assert loaded is not None
            assert loaded.status == TurnStatus.COMPLETED  # 第一次的值,不被覆盖

    async def test_double_finish_handle_is_noop(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
    ) -> None:
        writer = TraceWriter(sessionmaker)
        turn = await writer.begin_turn(provider_name="mock")
        pc = turn.begin_provider_call(provider_name="mock")
        await pc.finish(response_summary={"stop_reason": "end_turn"})
        await pc.finish(error_type="oops")  # 第二次 no-op
        await turn.finalize(status=TurnStatus.COMPLETED)

        async with sessionmaker() as session:
            tree = await TraceRepo(session).get_tree(turn.turn_id or "")
            assert tree is not None
            assert len(tree.provider_calls) == 1
            assert tree.provider_calls[0].error_type is None


class TestTraceWriterDisabled:
    async def test_sessionmaker_none_no_writes(self) -> None:
        """sessionmaker=None 时所有 handle 退化为 no-op,不抛错。"""
        writer = TraceWriter(None)
        turn = await writer.begin_turn(provider_name="mock")
        assert turn.turn_id is None  # disabled 状态
        pc = turn.begin_provider_call(provider_name="mock")
        await pc.finish(response_summary={"stop_reason": "end_turn"})
        tc = turn.begin_tool_call(tool_name="read_file")
        await tc.finish(status=ToolCallStatus.OK)
        await turn.record_checkpoint(kind=CheckpointKind.MANUAL)
        await turn.finalize(status=TurnStatus.COMPLETED)
        # 不抛错就是 happy path


class TestTraceWriterFailureTolerance:
    async def test_begin_turn_failure_does_not_raise(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """模拟 begin_turn 时 repo 抛错 → writer 不传播。"""
        writer = TraceWriter(sessionmaker)

        async def boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError("DB down")

        monkeypatch.setattr(TraceRepo, "create_turn", boom)
        turn = await writer.begin_turn(provider_name="mock")
        assert turn.turn_id is None  # 失败 → handle 退化
        # 派生事件 + finalize 都 no-op,不抛
        await turn.begin_provider_call(provider_name="mock").finish()
        await turn.begin_tool_call(tool_name="x").finish()
        await turn.finalize(status=TurnStatus.COMPLETED)

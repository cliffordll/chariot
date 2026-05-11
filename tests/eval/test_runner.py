"""EvalRunner —— stub agent 注入,端到端 happy path + 错误路径覆盖。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, ClassVar

import pytest_asyncio

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest
from chariot.agent.run import AIAgent
from chariot.database.session import dispose_db, init_db
from chariot.eval.report import EvalReport
from chariot.eval.runner import EvalRunner
from chariot.models.eval import GoldenTask, Verdict
from chariot.providers.base import BaseProvider, BaseProviderConfig
from chariot.tools.base import BaseTool


class _TextProvider(BaseProvider):
    """模拟 provider:单轮 text 响应,无工具调用。"""

    def __init__(self, *, text: str = "hello world") -> None:
        self.config = BaseProviderConfig(name="mock", model="mock-1")
        self._text = text

    @classmethod
    def create(cls, options: dict[str, Any]) -> _TextProvider:
        del options
        return cls()

    async def generate(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        del req
        yield ChatEvent.message_start(
            message_id="m1", model=self.config.model, usage={"input_tokens": 10, "output_tokens": 0}
        )
        yield ChatEvent.text_block_start(index=0)
        yield ChatEvent.text_delta(self._text, index=0)
        yield ChatEvent.block_stop(index=0)
        yield ChatEvent.message_delta_done(
            stop_reason="end_turn",
            usage={"input_tokens": 10, "output_tokens": 20},
        )
        yield ChatEvent.message_done()


class _ToolUsingProvider(BaseProvider):
    """模拟 provider:第一轮 tool_use,第二轮 text。"""

    def __init__(self, *, tool_name: str, tool_args: dict[str, Any], final_text: str = "done") -> None:
        self.config = BaseProviderConfig(name="mock", model="mock-1")
        self._tool_name = tool_name
        self._tool_args = tool_args
        self._final_text = final_text
        self.call_count = 0

    @classmethod
    def create(cls, options: dict[str, Any]) -> _ToolUsingProvider:
        del options
        return cls(tool_name="echo_tool", tool_args={"text": "hi"})

    async def generate(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        del req
        self.call_count += 1
        yield ChatEvent.message_start(message_id=f"m{self.call_count}", model=self.config.model)
        if self.call_count == 1:
            yield ChatEvent.tool_use_block_start(index=0, tool_use_id="tu_1", tool_name=self._tool_name)
            import json

            yield ChatEvent.input_json_delta(json.dumps(self._tool_args), index=0)
            yield ChatEvent.block_stop(index=0)
            yield ChatEvent.message_delta_done(stop_reason="tool_use")
        else:
            yield ChatEvent.text_block_start(index=0)
            yield ChatEvent.text_delta(self._final_text, index=0)
            yield ChatEvent.block_stop(index=0)
            yield ChatEvent.message_delta_done(stop_reason="end_turn", usage={"input_tokens": 20, "output_tokens": 10})
        yield ChatEvent.message_done()


class _EchoTool(BaseTool):
    name: ClassVar[str] = "echo_tool"

    @classmethod
    def create(cls, entry: Any) -> _EchoTool:
        del entry
        return cls()

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "echo",
            "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        }

    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        return {"type": "tool_result", "content": [{"type": "text", "text": str(input.get("text", ""))}]}


@pytest_asyncio.fixture
async def sessionmaker(tmp_path: Path):
    sm = await init_db(tmp_path / "test.db")
    try:
        yield sm
    finally:
        await dispose_db()


def _factory_returning(agent: AIAgent):
    def _f(task: GoldenTask) -> AIAgent:
        del task
        return agent

    return _f


async def test_exact_match_pass(sessionmaker) -> None:
    agent = AIAgent(providers={"mock": _TextProvider(text="hello there")}, tools={}, sessionmaker=sessionmaker)
    task = GoldenTask(
        task_id="t1",
        prompt="say hi",
        verifier_type="exact_match",
        expected={"contains": ["hello"]},
    )
    runner = EvalRunner(agent_factory=_factory_returning(agent))
    record = await runner.run_task(task)
    assert record.verdict is Verdict.PASS
    assert "hello there" in record.final_response
    # B1 trace 反查应填好 token / cost
    assert record.input_tokens == 10
    assert record.output_tokens == 20
    assert record.turns == 1


async def test_exact_match_fail(sessionmaker) -> None:
    agent = AIAgent(providers={"mock": _TextProvider(text="goodbye")}, tools={}, sessionmaker=sessionmaker)
    task = GoldenTask(
        task_id="t_miss",
        prompt="say hi",
        verifier_type="exact_match",
        expected={"contains": ["hello"]},
    )
    record = await EvalRunner(agent_factory=_factory_returning(agent)).run_task(task)
    assert record.verdict is Verdict.FAIL
    assert "hello" in record.reason


async def test_tool_called_pass(sessionmaker) -> None:
    """provider 第一轮 tool_use,工具循环跑 echo_tool,trace 记录 tool_call,verifier 命中。"""
    agent = AIAgent(
        providers={"mock": _ToolUsingProvider(tool_name="echo_tool", tool_args={"text": "hi"})},
        tools={"echo_tool": _EchoTool()},
        sessionmaker=sessionmaker,
    )
    task = GoldenTask(
        task_id="t_tool",
        prompt="please use echo_tool",
        verifier_type="tool_called",
        expected={"tool": "echo_tool", "args_subset": {"text": "hi"}},
    )
    record = await EvalRunner(agent_factory=_factory_returning(agent)).run_task(task)
    assert record.verdict is Verdict.PASS, f"got {record.verdict} / {record.reason}"
    assert any(tc["tool_name"] == "echo_tool" for tc in record.tool_calls)
    # 工具循环 2 轮 → turns == 2
    assert record.turns == 2


async def test_unknown_verifier_errors(sessionmaker) -> None:
    agent = AIAgent(providers={"mock": _TextProvider()}, tools={}, sessionmaker=sessionmaker)
    task = GoldenTask(task_id="t", prompt="x", verifier_type="bogus_verifier", expected={})
    record = await EvalRunner(agent_factory=_factory_returning(agent)).run_task(task)
    assert record.verdict is Verdict.ERROR
    assert "unknown verifier_type" in record.reason


async def test_run_all_serial_returns_one_record_per_task(sessionmaker) -> None:
    agent = AIAgent(providers={"mock": _TextProvider(text="hello")}, tools={}, sessionmaker=sessionmaker)
    tasks = [
        GoldenTask(task_id=f"t{i}", prompt="x", verifier_type="exact_match", expected={"contains": ["hello"]})
        for i in range(3)
    ]
    records = await EvalRunner(agent_factory=_factory_returning(agent)).run_all(tasks)
    assert len(records) == 3
    assert all(r.verdict is Verdict.PASS for r in records)
    # 每个 task 应该有自己独立的 turn_id(unique conversation_id)
    assert len({r.turn_id for r in records}) == 3


async def test_report_renders_summary(sessionmaker) -> None:
    agent = AIAgent(providers={"mock": _TextProvider(text="hello")}, tools={}, sessionmaker=sessionmaker)
    tasks = [
        GoldenTask(task_id="t1", prompt="x", verifier_type="exact_match", expected={"contains": ["hello"]}),
        GoldenTask(task_id="t2", prompt="x", verifier_type="exact_match", expected={"contains": ["missing"]}),
    ]
    records = await EvalRunner(agent_factory=_factory_returning(agent)).run_all(tasks)
    summary = EvalReport.summarize(records)
    assert summary.total == 2
    assert summary.passed == 1
    assert summary.failed == 1
    assert summary.pass_rate() == 0.5
    lines = EvalReport.render_lines(records)
    assert any("1/2 PASS" in line for line in lines)


def test_report_to_dict_serializable() -> None:
    """RunRecord 列表 → JSON-serializable dict(wave 4 落盘要求)。"""
    import json

    from chariot.models.eval import RunRecord

    records = [
        RunRecord(task_id="t1", verdict=Verdict.PASS, final_response="hi"),
        RunRecord(task_id="t2", verdict=Verdict.FAIL, reason="missing 'hello'", input_tokens=10, output_tokens=5),
    ]
    payload = EvalReport.to_dict(records)
    # 确认序列化不抛
    json.dumps(payload)
    assert payload["summary"]["total"] == 2
    assert payload["summary"]["passed"] == 1
    assert payload["records"][0]["task_id"] == "t1"
    assert payload["records"][1]["verdict"] == "FAIL"

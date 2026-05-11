"""CI smoke —— eval 完整 pipeline 端到端跑通(loader → runner → verifier → store → diff)。

不接真 model,用 stub provider + 1 个简单 task,跑完落盘,再加载回来 diff(基线 vs current),
确认五件套(loader / runner / verifier / store / diff)在同一调用链下不抛 / 输出可解析。

定位:这一个测试就是 wave 4 要求的 "CI smoke",CI 跑这个就能保证 B2 框架自身没坏。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest_asyncio

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest
from chariot.agent.run import AIAgent
from chariot.database.session import dispose_db, init_db
from chariot.eval.diff import DiffStatus, EvalDiff
from chariot.eval.loader import GoldenTaskLoader
from chariot.eval.report import EvalReport
from chariot.eval.runner import EvalRunner
from chariot.eval.store import EvalRunStore
from chariot.models.eval import GoldenTask, Verdict
from chariot.providers.base import BaseProvider, BaseProviderConfig


class _StubProvider(BaseProvider):
    """单轮 text 响应,可配置返回文案。"""

    def __init__(self, *, text: str = "hello world") -> None:
        self.config = BaseProviderConfig(name="mock", model="mock-1")
        self._text = text

    @classmethod
    def create(cls, options: dict[str, Any]) -> _StubProvider:
        del options
        return cls()

    async def generate(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        del req
        yield ChatEvent.message_start(message_id="m1", model=self.config.model, usage={"input_tokens": 5})
        yield ChatEvent.text_block_start(index=0)
        yield ChatEvent.text_delta(self._text, index=0)
        yield ChatEvent.block_stop(index=0)
        yield ChatEvent.message_delta_done(stop_reason="end_turn", usage={"input_tokens": 5, "output_tokens": 7})
        yield ChatEvent.message_done()


@pytest_asyncio.fixture
async def sessionmaker(tmp_path: Path):
    sm = await init_db(tmp_path / "test.db")
    try:
        yield sm
    finally:
        await dispose_db()


def _write_yaml(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


async def test_full_pipeline_load_run_save_diff(tmp_path: Path, sessionmaker) -> None:
    # 1. seed 1 个 golden task
    golden_dir = tmp_path / "golden"
    golden_dir.mkdir()
    _write_yaml(
        golden_dir / "smoke.yaml",
        """
task_id: smoke_oneshot
prompt: say hi
verifier_type: exact_match
expected:
  contains: [hello]
category: smoke
""",
    )

    # 2. loader 加载
    tasks = GoldenTaskLoader.load_dir(golden_dir)
    assert len(tasks) == 1

    # 3. runner 跑(stub agent 返 "hello world",exact_match 应 PASS)
    agent_pass = AIAgent(providers={"mock": _StubProvider(text="hello world")}, tools={}, sessionmaker=sessionmaker)

    def _factory_pass(_t: GoldenTask) -> AIAgent:
        return agent_pass

    runner_pass = EvalRunner(agent_factory=_factory_pass)
    pass_records = await runner_pass.run_all(tasks)
    assert pass_records[0].verdict is Verdict.PASS

    # 4. report 渲染不抛
    lines = EvalReport.render_lines(pass_records)
    assert any("PASS" in line for line in lines)

    # 5. store 落盘(注入 tmp_path 当 root,不污染真实 ~/.chariot/eval/)
    store = EvalRunStore(root=tmp_path / "runs")
    baseline_id = store.save(tasks=tasks, records=pass_records)
    assert (tmp_path / "runs" / baseline_id / "report.txt").is_file()
    assert (tmp_path / "runs" / baseline_id / "records.json").is_file()
    assert (tmp_path / "runs" / baseline_id / "summary.json").is_file()
    assert (tmp_path / "runs" / baseline_id / "tasks.json").is_file()
    assert (tmp_path / "runs" / baseline_id / "meta.json").is_file()

    # 6. 再跑一次(用 fail 文案),save 后跟 baseline diff
    agent_fail = AIAgent(providers={"mock": _StubProvider(text="goodbye")}, tools={}, sessionmaker=sessionmaker)

    def _factory_fail(_t: GoldenTask) -> AIAgent:
        return agent_fail

    fail_records = await EvalRunner(agent_factory=_factory_fail).run_all(tasks)
    assert fail_records[0].verdict is Verdict.FAIL

    cur_id = store.save(tasks=tasks, records=fail_records)
    baseline_snap = store.load(baseline_id)
    cur_snap = store.load(cur_id)
    assert baseline_snap is not None
    assert cur_snap is not None

    entries = EvalDiff.compute(baseline=baseline_snap, current=cur_snap)
    assert entries[0].status is DiffStatus.REGRESSED
    summary = EvalDiff.summarize(entries)
    assert summary.regressed == 1
    assert summary.total_changes == 1

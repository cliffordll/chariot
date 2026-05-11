"""sidecar eval RPC dispatch tests(B2 wave 5)。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from chariot.agent.registry import AgentRegistry
from chariot.agent.run import AIAgent
from chariot.database.session import dispose_db
from chariot.eval.store import EvalRunStore
from chariot.models.eval import GoldenTask, RunRecord, Verdict
from chariot.rpc.jsonrpc import JsonRpcServer
from chariot.sidecar.methods import register_methods


def make_reader(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


class MockWriter:
    def __init__(self) -> None:
        self.buf = bytearray()

    def write(self, data: bytes) -> None:
        self.buf.extend(data)

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def lines(self) -> list[dict[str, Any]]:
        return [json.loads(s) for s in self.buf.split(b"\n") if s.strip()]


def _request_frame(rid: int, method: str, params: dict[str, Any] | None = None) -> bytes:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        body["params"] = params
    return (json.dumps(body) + "\n").encode()


@pytest_asyncio.fixture
async def agent(tmp_path: Path):
    a = await AIAgent.bootstrap(tmp_path / "test.db")
    AgentRegistry._agents.clear()
    try:
        yield a
    finally:
        AgentRegistry._agents.clear()
        await dispose_db()


@pytest.fixture
def server(agent: AIAgent, tmp_path: Path) -> JsonRpcServer:
    s = JsonRpcServer()
    register_methods(s, agent, db_path=tmp_path / "test.db")
    return s


def _seed_run(runs_dir: Path, *, run_records: dict[str, Verdict]) -> str:
    store = EvalRunStore(root=runs_dir)
    tasks = [
        GoldenTask(
            task_id=tid,
            prompt="p",
            verifier_type="exact_match",
            expected={"contains": ["hi"]},
            category="smoke",
        )
        for tid in run_records
    ]
    records = [RunRecord(task_id=tid, verdict=v, final_response="ok") for tid, v in run_records.items()]
    return store.save(tasks=tasks, records=records)


async def _call(server: JsonRpcServer, method: str, params: dict[str, Any]) -> dict[str, Any]:
    writer = MockWriter()
    await server.serve(make_reader(_request_frame(1, method, params)), writer)
    return writer.lines()[0]


@pytest.mark.asyncio
async def test_list_golden_tasks_returns_loaded(server: JsonRpcServer, tmp_path: Path) -> None:
    golden_dir = tmp_path / "golden"
    golden_dir.mkdir()
    (golden_dir / "t.yaml").write_text(
        "task_id: t1\nprompt: x\nverifier_type: exact_match\nexpected: {contains: [hi]}\ncategory: smoke\n",
        encoding="utf-8",
    )
    resp = await _call(server, "list_golden_tasks", {"golden_dir": str(golden_dir)})
    assert "result" in resp
    assert resp["result"]["missing"] is False
    assert len(resp["result"]["tasks"]) == 1
    assert resp["result"]["tasks"][0]["task_id"] == "t1"


@pytest.mark.asyncio
async def test_list_golden_tasks_missing_dir_returns_empty(server: JsonRpcServer, tmp_path: Path) -> None:
    resp = await _call(server, "list_golden_tasks", {"golden_dir": str(tmp_path / "ghost")})
    assert resp["result"]["missing"] is True
    assert resp["result"]["tasks"] == []


@pytest.mark.asyncio
async def test_list_eval_runs_returns_summary(server: JsonRpcServer, tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    _seed_run(runs_dir, run_records={"t1": Verdict.PASS, "t2": Verdict.FAIL})
    resp = await _call(server, "list_eval_runs", {"runs_dir": str(runs_dir)})
    runs = resp["result"]["runs"]
    assert len(runs) == 1
    assert runs[0]["summary"]["total"] == 2
    assert runs[0]["summary"]["passed"] == 1


@pytest.mark.asyncio
async def test_get_eval_run_returns_full_snapshot(server: JsonRpcServer, tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    run_id = _seed_run(runs_dir, run_records={"t1": Verdict.PASS})
    resp = await _call(server, "get_eval_run", {"run_id": run_id, "runs_dir": str(runs_dir)})
    assert resp["result"]["run_id"] == run_id
    assert len(resp["result"]["records"]) == 1
    assert resp["result"]["records"][0]["verdict"] == "PASS"


@pytest.mark.asyncio
async def test_get_eval_run_not_found(server: JsonRpcServer, tmp_path: Path) -> None:
    resp = await _call(server, "get_eval_run", {"run_id": "ghost", "runs_dir": str(tmp_path / "runs")})
    assert resp["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND


@pytest.mark.asyncio
async def test_diff_eval_runs_returns_entries(server: JsonRpcServer, tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    base_id = _seed_run(runs_dir, run_records={"t1": Verdict.PASS, "t2": Verdict.PASS})
    cur_id = _seed_run(runs_dir, run_records={"t1": Verdict.PASS, "t2": Verdict.FAIL})
    resp = await _call(
        server,
        "diff_eval_runs",
        {"baseline_id": base_id, "current_id": cur_id, "runs_dir": str(runs_dir)},
    )
    assert resp["result"]["summary"]["regressed"] == 1
    assert resp["result"]["summary"]["stable"] == 1
    entries = {e["task_id"]: e["status"] for e in resp["result"]["entries"]}
    assert entries["t1"] == "STABLE"
    assert entries["t2"] == "REGRESSED"


@pytest.mark.asyncio
async def test_diff_eval_runs_unknown_baseline(server: JsonRpcServer, tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    cur_id = _seed_run(runs_dir, run_records={"t1": Verdict.PASS})
    resp = await _call(
        server,
        "diff_eval_runs",
        {"baseline_id": "ghost", "current_id": cur_id, "runs_dir": str(runs_dir)},
    )
    assert resp["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND

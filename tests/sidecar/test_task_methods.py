"""Sidecar task method tests for A6 read surfaces."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from chariot.agent.registry import AgentRegistry
from chariot.agent.run import AIAgent
from chariot.database.session import dispose_db
from chariot.models.task import TaskCreate, TaskRunCreate
from chariot.repos.provider_repo import ProviderRepo
from chariot.rpc.jsonrpc import JsonRpcServer
from chariot.services.agent import AgentService
from chariot.services.job import JobService
from chariot.services.task import TaskService
from chariot.sidecar.methods import register_methods


def _make_reader(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


class _Writer:
    def __init__(self) -> None:
        self.buf = bytearray()

    def write(self, data: bytes) -> None:
        self.buf.extend(data)

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def lines(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.buf.split(b"\n") if line.strip()]


async def _call(server: JsonRpcServer, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    reader = _make_reader((json.dumps(body) + "\n").encode())
    writer = _Writer()
    await server.serve(reader, writer)
    return writer.lines()[0]


@pytest_asyncio.fixture(autouse=True)
async def _isolate() -> AsyncIterator[None]:
    await AgentRegistry.clear()
    await dispose_db()
    yield
    await AgentRegistry.clear()
    await dispose_db()


@pytest_asyncio.fixture
async def agent(tmp_path: Path) -> AIAgent:
    return await AIAgent.bootstrap(tmp_path / "chariot.db")


@pytest.fixture
def server(agent: AIAgent, tmp_path: Path) -> JsonRpcServer:
    server = JsonRpcServer()
    register_methods(server, agent, db_path=tmp_path / "chariot.db")
    return server


@pytest.mark.asyncio
async def test_list_agents_returns_created_profiles(server: JsonRpcServer, agent: AIAgent) -> None:
    await AgentService(agent).create_agent(
        name="planner",
        role="planner",
        tool_profile="default",
        provider_id="mock",
    )

    line = await _call(server, "list_agents")
    agents = line["result"]["agents"]
    assert agents[0]["name"] == "planner"
    assert agents[0]["role"] == "planner"


@pytest.mark.asyncio
async def test_create_and_get_agent(server: JsonRpcServer) -> None:
    line = await _call(
        server,
        "create_agent",
        {
            "name": "planner",
            "role": "planner",
            "tool_profile": "default",
            "provider_id": "mock",
            "budget": {"max_steps": 5},
        },
    )
    agent = line["result"]["agent"]
    assert agent["name"] == "planner"
    assert agent["provider_id"]
    assert agent["budget"]["max_steps"] == 5

    detail = await _call(server, "get_agent", {"name": "planner"})
    assert detail["result"]["agent"]["role"] == "planner"


@pytest.mark.asyncio
async def test_update_and_delete_agent(server: JsonRpcServer) -> None:
    await _call(server, "create_agent", {"name": "planner", "role": "planner"})

    updated = await _call(
        server,
        "update_agent",
        {"name": "planner", "role": "executor", "tool_profile": "default"},
    )
    assert updated["result"]["agent"]["role"] == "executor"
    assert updated["result"]["agent"]["tool_profile"] == "default"

    deleted = await _call(server, "delete_agent", {"name": "planner"})
    assert deleted["result"]["deleted"] == "planner"

    detail = await _call(server, "get_agent", {"name": "planner"})
    assert detail["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND


@pytest.mark.asyncio
async def test_update_agent_clear_binding_fields(server: JsonRpcServer) -> None:
    """RPC payload key 不在 → UNSET(skip);key=null → 清空;key=str → set。"""
    await _call(
        server,
        "create_agent",
        {
            "name": "researcher",
            "role": "research",
            "prompt_bundle": "research",
            "tool_profile": "fs_safe",
            "provider_id": "claude",
        },
    )

    # 只改 role,binding 字段 key 不在 payload 里 → 保留
    only_role = await _call(server, "update_agent", {"name": "researcher", "role": "planner"})
    assert only_role["result"]["agent"]["role"] == "planner"
    assert only_role["result"]["agent"]["provider_id"] == "claude"
    assert only_role["result"]["agent"]["prompt_bundle"] == "research"

    # provider_id=null → 清空,其它保留
    cleared = await _call(
        server,
        "update_agent",
        {"name": "researcher", "provider_id": None},
    )
    assert cleared["result"]["agent"]["provider_id"] is None
    assert cleared["result"]["agent"]["prompt_bundle"] == "research"
    assert cleared["result"]["agent"]["tool_profile"] == "fs_safe"

    # 重新 set
    reset = await _call(
        server,
        "update_agent",
        {"name": "researcher", "provider_id": "ollama"},
    )
    assert reset["result"]["agent"]["provider_id"] == "ollama"


@pytest.mark.asyncio
async def test_update_agent_accepts_provider_id_alias(server: JsonRpcServer, agent: AIAgent) -> None:
    await _call(server, "create_agent", {"name": "planner", "role": "planner"})
    updated = await _call(
        server,
        "update_agent",
        {"name": "planner", "provider_id": "mock"},
    )
    async with agent._sessionmaker() as session:  # type: ignore[attr-defined]
        mock = await ProviderRepo(session).get_entry("mock")
    assert mock is not None
    entry = updated["result"]["agent"]
    assert entry["provider_id"] == mock.id


@pytest.mark.asyncio
async def test_list_tasks_and_get_task_include_runs_and_children(server: JsonRpcServer, agent: AIAgent) -> None:
    await AgentService(agent).create_agent(name="planner", role="planner")
    service = TaskService(agent)
    parent = await service.create_task(TaskCreate(goal="parent", agent_profile="planner"))
    child = await service.create_child_task(parent_task_id=parent.id, goal="child")
    run = await service.start_task_run(TaskRunCreate(task_id=parent.id))
    await service.complete_task_run(run.id, result={"ok": True})

    line = await _call(server, "list_tasks")
    tasks = line["result"]["tasks"]
    assert any(task["id"] == parent.id for task in tasks)
    assert any(task["id"] == child.id for task in tasks)

    detail = await _call(server, "get_task", {"task_id": parent.id})
    task = detail["result"]["task"]
    assert task["id"] == parent.id
    assert task["child_status_summary"]["queued"] == 1
    assert task["runs"][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_list_jobs_returns_created_jobs(server: JsonRpcServer, agent: AIAgent) -> None:
    await JobService(agent).create_job(
        name="cleanup",
        goal="cleanup stale state",
        cron="0 * * * *",
    )

    line = await _call(server, "list_jobs")
    jobs = line["result"]["jobs"]
    assert jobs[0]["name"] == "cleanup"
    assert jobs[0]["enabled"] is True


@pytest.mark.asyncio
async def test_show_job_and_run_job_now(server: JsonRpcServer, agent: AIAgent) -> None:
    await JobService(agent).create_job(
        name="cleanup",
        goal="cleanup stale state",
        cron="0 * * * *",
    )

    line = await _call(server, "run_job_now", {"name": "cleanup"})
    assert line["result"]["task"]["kind"] == "scheduled"
    assert line["result"]["job_run"]["job_name"] == "cleanup"
    assert line["result"]["job_run"]["status"] == "queued"

    detail = await _call(server, "show_job", {"name": "cleanup"})
    job = detail["result"]["job"]
    assert job["name"] == "cleanup"
    assert job["last_run_status"] == "queued"
    assert job["runs"][0]["job_name"] == "cleanup"


@pytest.mark.asyncio
async def test_create_update_enable_disable_job(server: JsonRpcServer) -> None:
    created = await _call(
        server,
        "create_job",
        {
            "name": "cleanup",
            "goal": "cleanup stale state",
            "cron": "0 * * * *",
            "enabled": False,
            "meta": {"scope": "repo"},
        },
    )
    job = created["result"]["job"]
    assert job["name"] == "cleanup"
    assert job["enabled"] is False
    assert job["meta"]["scope"] == "repo"

    updated = await _call(
        server,
        "update_job",
        {"name": "cleanup", "goal": "cleanup tmp files", "cron": "*/5 * * * *"},
    )
    assert updated["result"]["job"]["goal"] == "cleanup tmp files"
    assert updated["result"]["job"]["cron"] == "*/5 * * * *"

    enabled = await _call(server, "enable_job", {"name": "cleanup"})
    assert enabled["result"]["job"]["enabled"] is True

    disabled = await _call(server, "disable_job", {"name": "cleanup"})
    assert disabled["result"]["job"]["enabled"] is False

    deleted = await _call(server, "delete_job", {"name": "cleanup"})
    assert deleted["result"]["deleted"] == "cleanup"


@pytest.mark.asyncio
async def test_get_task_not_found_returns_not_found(server: JsonRpcServer) -> None:
    line = await _call(server, "get_task", {"task_id": "ghost"})
    assert line["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND


@pytest.mark.asyncio
async def test_create_task_returns_queued_task(server: JsonRpcServer) -> None:
    line = await _call(
        server,
        "create_task",
        {"goal": "plan next step", "kind": "interactive", "owner": "user"},
    )
    task = line["result"]["task"]
    assert task["goal"] == "plan next step"
    assert task["status"] == "queued"
    assert task["kind"] == "interactive"


@pytest.mark.asyncio
async def test_pause_resume_cancel_task_flow(server: JsonRpcServer, agent: AIAgent) -> None:
    service = TaskService(agent)
    task = await service.create_task(TaskCreate(goal="worker"))
    await service.start_task_run(TaskRunCreate(task_id=task.id))

    paused = await _call(server, "pause_task", {"task_id": task.id})
    assert paused["result"]["task"]["status"] == "paused"

    resumed = await _call(server, "resume_task", {"task_id": task.id})
    assert resumed["result"]["task"]["status"] == "running"

    cancelled = await _call(server, "cancel_task", {"task_id": task.id})
    assert cancelled["result"]["task"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_start_and_complete_task_run(server: JsonRpcServer, agent: AIAgent) -> None:
    service = TaskService(agent)
    task = await service.create_task(TaskCreate(goal="worker"))

    started = await _call(server, "start_task_run", {"task_id": task.id, "trigger": "manual"})
    run = started["result"]["run"]
    assert run["task_id"] == task.id
    assert run["status"] == "running"

    completed = await _call(server, "complete_task_run", {"run_id": run["id"], "result": {"ok": True}})
    assert completed["result"]["run"]["status"] == "completed"

    detail = await _call(server, "get_task", {"task_id": task.id})
    assert detail["result"]["task"]["status"] == "completed"


@pytest.mark.asyncio
async def test_get_and_list_task_runs(server: JsonRpcServer, agent: AIAgent) -> None:
    service = TaskService(agent)
    task = await service.create_task(TaskCreate(goal="worker"))

    started = await _call(server, "start_task_run", {"task_id": task.id, "trigger": "manual"})
    run = started["result"]["run"]

    listed = await _call(server, "list_task_runs", {"task_id": task.id})
    assert listed["result"]["runs"][0]["id"] == run["id"]

    detail = await _call(server, "get_task_run", {"run_id": run["id"]})
    assert detail["result"]["run"]["task_id"] == task.id


@pytest.mark.asyncio
async def test_fail_task_run_marks_run_and_task_failed(server: JsonRpcServer, agent: AIAgent) -> None:
    service = TaskService(agent)
    task = await service.create_task(TaskCreate(goal="worker"))

    started = await _call(server, "start_task_run", {"task_id": task.id, "trigger": "manual"})
    run = started["result"]["run"]

    failed = await _call(server, "fail_task_run", {"run_id": run["id"], "error": "boom"})
    assert failed["result"]["run"]["status"] == "failed"
    assert failed["result"]["run"]["error"] == "boom"

    detail = await _call(server, "get_task", {"task_id": task.id})
    assert detail["result"]["task"]["status"] == "failed"


@pytest.mark.asyncio
async def test_cancel_task_run_marks_run_and_task_cancelled(server: JsonRpcServer, agent: AIAgent) -> None:
    service = TaskService(agent)
    task = await service.create_task(TaskCreate(goal="worker"))

    started = await _call(server, "start_task_run", {"task_id": task.id, "trigger": "manual"})
    run = started["result"]["run"]

    cancelled = await _call(server, "cancel_task_run", {"run_id": run["id"], "error": "user requested"})
    assert cancelled["result"]["run"]["status"] == "cancelled"
    assert cancelled["result"]["run"]["error"] == "user requested"

    detail = await _call(server, "get_task", {"task_id": task.id})
    assert detail["result"]["task"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_delegate_task_creates_children(server: JsonRpcServer, agent: AIAgent) -> None:
    service = TaskService(agent)
    parent = await service.create_task(TaskCreate(goal="parent goal", agent_profile="planner"))

    line = await _call(
        server,
        "delegate_task",
        {
            "parent_task_id": parent.id,
            "reason": "split work",
            "tasks": [
                {"goal": "child a"},
                {"goal": "child b", "meta": {"priority": "high"}},
            ],
        },
    )
    delegation = line["result"]["delegation"]
    assert delegation["parent_task_id"] == parent.id
    assert delegation["requested"] == 2
    assert delegation["created"] == 2

    children = await _call(server, "list_tasks", {"parent_task_id": parent.id})
    child_tasks = children["result"]["tasks"]
    assert len(child_tasks) == 2
    assert all(task["parent_task_id"] == parent.id for task in child_tasks)


@pytest.mark.asyncio
async def test_delegate_task_on_missing_parent_returns_not_found(server: JsonRpcServer) -> None:
    line = await _call(
        server,
        "delegate_task",
        {"parent_task_id": "ghost", "tasks": [{"goal": "child"}]},
    )
    assert line["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND

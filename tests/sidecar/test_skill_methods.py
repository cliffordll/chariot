"""B6 wave 1 step 1b —— sidecar `list_skills` / `get_skill` / `install_skill` /
`enable_skill` / `disable_skill` / `delete_skill` + audit hook 串联。"""

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
from chariot.audit.hooks import AuditHookManager
from chariot.database.session import dispose_db
from chariot.rpc.jsonrpc import JsonRpcServer
from chariot.services.audit import AuditService
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


def _frame(rid: int, method: str, params: dict[str, Any] | None = None) -> bytes:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        body["params"] = params
    return (json.dumps(body) + "\n").encode()


@pytest_asyncio.fixture
async def agent(tmp_path: Path) -> AsyncIterator[AIAgent]:
    a = await AIAgent.bootstrap(tmp_path / "skill_methods.db")
    AgentRegistry._agents.clear()
    try:
        yield a
    finally:
        AgentRegistry._agents.clear()
        await dispose_db()


@pytest.fixture
def server(agent: AIAgent, tmp_path: Path) -> JsonRpcServer:
    s = JsonRpcServer()
    register_methods(s, agent, db_path=tmp_path / "skill_methods.db")
    return s


async def _call(server: JsonRpcServer, method: str, params: dict[str, Any]) -> dict[str, Any]:
    writer = MockWriter()
    await server.serve(make_reader(_frame(1, method, params)), writer)
    return writer.lines()[0]


# ---- list / show ----


async def test_list_skills_returns_3_builtin(server: JsonRpcServer) -> None:
    resp = await _call(server, "list_skills", {})
    assert "result" in resp
    skills = resp["result"]["skills"]
    names = {s["name"] for s in skills}
    assert {"code_review", "debug_helper", "git_committer"}.issubset(names)
    # builtin source 标识
    for s in skills:
        if s["name"] in {"code_review", "debug_helper", "git_committer"}:
            assert s["source"] == "builtin"
            assert s["enabled"] is True


async def test_get_skill_returns_full_manifest(server: JsonRpcServer) -> None:
    resp = await _call(server, "get_skill", {"name": "code_review"})
    assert "result" in resp
    skill = resp["result"]["skill"]
    assert skill["name"] == "code_review"
    assert "prompt" in skill  # full=True 才有 prompt
    assert "allowed_tools" in skill


async def test_get_skill_missing_returns_not_found(server: JsonRpcServer) -> None:
    resp = await _call(server, "get_skill", {"name": "does_not_exist"})
    assert resp["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND


# ---- install_skill ----


_VALID_YAML = """
schema_version: 1
name: my_custom_skill
description: a tester
prompt: do the thing
"""


async def test_install_skill_from_content(server: JsonRpcServer) -> None:
    resp = await _call(server, "install_skill", {"content": _VALID_YAML})
    assert "result" in resp
    entry = resp["result"]["skill"]
    assert entry["name"] == "my_custom_skill"
    assert entry["enabled"] is True


async def test_install_skill_from_builtin_fork(server: JsonRpcServer) -> None:
    resp = await _call(server, "install_skill", {"from_builtin": "code_review"})
    assert "result" in resp
    assert resp["result"]["skill"]["name"] == "code_review"


async def test_install_skill_requires_one_param(server: JsonRpcServer) -> None:
    resp = await _call(server, "install_skill", {})
    assert resp["error"]["code"] == JsonRpcServer.ERR_INVALID_PARAMS


async def test_install_skill_rejects_both_params(server: JsonRpcServer) -> None:
    resp = await _call(
        server,
        "install_skill",
        {"content": _VALID_YAML, "from_builtin": "code_review"},
    )
    assert resp["error"]["code"] == JsonRpcServer.ERR_INVALID_PARAMS


async def test_install_skill_rejects_bad_yaml(server: JsonRpcServer) -> None:
    resp = await _call(server, "install_skill", {"content": "schema_version: 1\nname: BAD\ndescription: x\nprompt: x"})
    assert resp["error"]["code"] == JsonRpcServer.ERR_INVALID_PARAMS


# ---- curate_skills (B6 wave 4) ----


async def test_curate_skills_returns_four_buckets(server: JsonRpcServer) -> None:
    """curate 应返 4 个 list 字段(builtin 装 3 个 → 全部 stale,无 activations)。"""
    resp = await _call(server, "curate_skills", {})
    assert "result" in resp
    result = resp["result"]
    assert set(result.keys()) == {"stale", "underused", "failing", "overlapping"}
    # builtin 3 个,无 audit activations → 全在 stale + underused
    assert "code_review" in result["stale"]
    assert "debug_helper" in result["stale"]
    assert "git_committer" in result["stale"]
    assert "code_review" in result["underused"]
    # failing 是空(无样本)
    assert result["failing"] == []
    # overlapping 是 dict list(每条 {a, b, ratio})
    assert isinstance(result["overlapping"], list)


# ---- enable / disable / delete ----


async def test_enable_disable_db_skill(server: JsonRpcServer) -> None:
    await _call(server, "install_skill", {"content": _VALID_YAML, "enabled": False})
    resp = await _call(server, "enable_skill", {"name": "my_custom_skill"})
    assert "result" in resp
    assert resp["result"]["skill"]["enabled"] is True
    resp = await _call(server, "disable_skill", {"name": "my_custom_skill"})
    assert resp["result"]["skill"]["enabled"] is False


async def test_enable_builtin_returns_not_found(server: JsonRpcServer) -> None:
    """builtin skill 没在 DB 表里 → enable 找不到 row。"""
    resp = await _call(server, "enable_skill", {"name": "code_review"})
    assert resp["error"]["code"] == JsonRpcServer.ERR_NOT_FOUND


async def test_delete_skill(server: JsonRpcServer) -> None:
    await _call(server, "install_skill", {"content": _VALID_YAML})
    resp = await _call(server, "delete_skill", {"name": "my_custom_skill"})
    assert resp["result"]["deleted"] == "my_custom_skill"


# ---- audit hook 串联 ----


async def test_install_skill_writes_audit_event(server: JsonRpcServer, agent: AIAgent) -> None:
    await _call(server, "install_skill", {"content": _VALID_YAML})
    events = await AuditService(agent).list_events(limit=10)
    skill_events = [e for e in events if e.event_type == AuditHookManager.EVENT_SKILL_STORE]
    assert len(skill_events) >= 1
    payload = skill_events[0].payload
    assert payload["action"] == "create"
    assert payload["name"] == "my_custom_skill"
    assert payload["source"] == "manual"

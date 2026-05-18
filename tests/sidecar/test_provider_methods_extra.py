"""Sidecar provider method tests for A5 surfaces."""

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
from chariot.rpc.jsonrpc import JsonRpcServer
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
async def test_show_provider_includes_capabilities(server: JsonRpcServer) -> None:
    line = await _call(server, "show_provider", {"name": "mock"})
    provider = line["result"]["provider"]
    assert provider["name"] == "Mock"
    assert provider["slug"] == "mock"
    assert provider["capabilities"]["supports_system"] is False


@pytest.mark.asyncio
async def test_get_provider_status_returns_summary(server: JsonRpcServer) -> None:
    line = await _call(server, "get_provider_status")
    status = line["result"]
    assert status["default_provider"] == "Mock (mock)"
    assert status["provider_count"] == 1
    assert status["providers"][0]["name"] == "Mock"
    assert status["providers"][0]["slug"] == "mock"


@pytest.mark.asyncio
async def test_use_provider_switches_default(server: JsonRpcServer) -> None:
    line = await _call(
        server,
        "add_provider",
        {"name": "mock2", "type": "mock", "options": {}, "params": {}},
    )
    provider = line["result"]["provider"]
    assert provider["name"] == "mock2"
    slug = provider["slug"]

    line = await _call(server, "use_provider", {"name": slug})
    provider = line["result"]["provider"]
    assert provider["name"] == "mock2"
    assert provider["default"] is True

    status = await _call(server, "get_provider_status")
    assert status["result"]["default_provider"] == "mock2 (mock-mock2)"
    assert any(p["name"] == "mock2" and p["default"] is True for p in status["result"]["providers"])


@pytest.mark.asyncio
async def test_probe_provider_updates_health(server: JsonRpcServer) -> None:
    line = await _call(server, "probe_provider", {"name": "mock"})
    assert line["result"]["ok"] is True

    status = await _call(server, "get_provider_status")
    provider = next(p for p in status["result"]["providers"] if p["slug"] == "mock")
    assert provider["health"]["last_ok"] is True
    assert provider["health"]["latency_ms"] is not None

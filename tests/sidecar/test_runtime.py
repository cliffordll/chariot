from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from chariot.agent.registry import AgentRegistry
from chariot.agent.run import AIAgent
from chariot.database.session import dispose_db
from chariot.repos.tool_repo import ToolRepo
from chariot.sidecar.runtime import SidecarRuntime


@pytest_asyncio.fixture(autouse=True)
async def _isolate() -> None:
    await AgentRegistry.clear()
    await dispose_db()
    yield
    await AgentRegistry.clear()
    await dispose_db()


@pytest.mark.asyncio
async def test_reload_refreshes_default_agent_tools(tmp_path: Path) -> None:
    db_path = tmp_path / "chariot.db"
    agent = await AIAgent.bootstrap(db_path)
    runtime = SidecarRuntime(agent, db_path=db_path)

    assert runtime.agent.tools == {}

    async with runtime.session_maker() as session:
        await ToolRepo(session).update("list_dir", enabled=True)

    await runtime.reload()

    assert "list_dir" in runtime.agent.tools


@pytest.mark.asyncio
async def test_reload_invalidates_override_agent_cache(tmp_path: Path) -> None:
    db_path = tmp_path / "chariot.db"
    agent = await AIAgent.bootstrap(db_path)
    runtime = SidecarRuntime(agent, db_path=db_path)

    _ = await runtime.reserve_chat_agent("mock", base_url="https://override.example", api_key=None)
    assert AgentRegistry.size() == 1

    await runtime.reload()

    assert AgentRegistry.size() == 0

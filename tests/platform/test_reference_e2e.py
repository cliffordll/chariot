"""B3 wave 3 — AIAgent + ReferenceExpander 端到端集成。

验证:bootstrap 装 ReferenceExpander 后,user message 里的 `@file:` 会展开成
`<reference type=file>` 块送给 provider。MockProvider echo 末轮 user content
→ 在响应里能看到 reference 内容,证明 expansion 发生在 provider call 之前。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio

from chariot.agent.chat_request import ChatRequest, Message
from chariot.agent.registry import AgentRegistry
from chariot.agent.run import AIAgent
from chariot.database.session import dispose_db


@pytest_asyncio.fixture
async def agent(tmp_path: Path) -> AsyncIterator[AIAgent]:
    a = await AIAgent.bootstrap(tmp_path / "test.db")
    AgentRegistry._agents.clear()
    try:
        yield a
    finally:
        AgentRegistry._agents.clear()
        await dispose_db()


async def test_file_reference_expanded_into_user_message(tmp_path: Path, agent: AIAgent) -> None:
    """`@file:` 被展开成 <reference> 块,MockProvider echo 时能看到 content。"""
    # 把 expander cwd 锚到 tmp_path 并写一个文件
    target = tmp_path / "note.txt"
    target.write_text("HELLO FROM FILE", encoding="utf-8")
    # bootstrap 默认锚 Path.cwd();替换 expander 锚到 tmp_path
    from chariot.context.references import ReferenceExpander

    agent._reference_expander = ReferenceExpander(
        cwd=tmp_path,
        allowed_domains=frozenset(),
        sessionmaker=agent.session_maker,
    )

    req = ChatRequest(
        provider_name="mock",
        messages=[Message(role="user", content="see @file:note.txt please")],
    )
    text_chunks: list[str] = []
    async for ev in agent.run_chat(req):
        if ev.kind == "content_block_delta":
            delta = ev.delta or {}
            if delta.get("type") == "text_delta":
                text_chunks.append(delta.get("text", ""))
    echo = "".join(text_chunks)
    assert "HELLO FROM FILE" in echo
    assert "<reference" in echo
    assert 'type="file"' in echo


async def test_unknown_reference_type_passes_through(agent: AIAgent) -> None:
    """非内建 type(如 @custom:x)pattern 不匹配 → 原样保留,不报错。"""
    req = ChatRequest(
        provider_name="mock",
        messages=[Message(role="user", content="@custom:x raw text")],
    )
    text_chunks: list[str] = []
    async for ev in agent.run_chat(req):
        if ev.kind == "content_block_delta":
            delta = ev.delta or {}
            if delta.get("type") == "text_delta":
                text_chunks.append(delta.get("text", ""))
    echo = "".join(text_chunks)
    assert "@custom:x" in echo  # 原样保留


async def test_file_not_found_yields_error_block(tmp_path: Path, agent: AIAgent) -> None:
    from chariot.context.references import ReferenceExpander

    agent._reference_expander = ReferenceExpander(
        cwd=tmp_path,
        allowed_domains=frozenset(),
        sessionmaker=agent.session_maker,
    )
    req = ChatRequest(
        provider_name="mock",
        messages=[Message(role="user", content="@file:ghost.md")],
    )
    text_chunks: list[str] = []
    async for ev in agent.run_chat(req):
        if ev.kind == "content_block_delta":
            delta = ev.delta or {}
            if delta.get("type") == "text_delta":
                text_chunks.append(delta.get("text", ""))
    echo = "".join(text_chunks)
    assert 'error="not_found"' in echo

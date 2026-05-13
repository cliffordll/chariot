from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.chat_request import ChatRequest, Message
from chariot.database.session import dispose_db, init_db
from chariot.repos.prompt_repo import PromptRepo


@pytest_asyncio.fixture
async def session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    db_path = tmp_path / "chariot.db"
    sm = await init_db(db_path)
    async with sm() as session:
        yield session
    await dispose_db()


async def test_prompt_bundle_create_update_activate(session: AsyncSession) -> None:
    repo = PromptRepo(session)
    await repo.seed_if_empty()

    created = await repo.create_bundle(
        "demo",
        description="demo prompt",
        layers=[{"name": "base_system", "source": "user", "content": "hello"}],
    )
    assert created.bundle_name == "demo"
    assert created.is_active is True

    updated = await repo.update_bundle(
        "demo",
        description="demo v2",
        layers=[{"name": "base_system", "source": "user", "content": "hello v2"}],
    )
    assert updated.version == "v2"

    active_bundle = await repo.get_active_bundle()
    assert active_bundle is not None
    assert active_bundle.name == "demo"

    trace = await repo.record_trace(
        ChatRequest(provider_name="mock", messages=[Message(role="user", content="hi")]),
        provider_name="mock",
        model="mock-1",
    )
    assert trace.bundle_name == "demo"
    assert trace.version == "v2"


async def test_update_bundle_same_content_no_new_version(session: AsyncSession) -> None:
    """update_bundle 内容相同(忽略字段顺序)时不创建新版本。"""
    repo = PromptRepo(session)
    await repo.seed_if_empty()

    # 创建初始 bundle
    created = await repo.create_bundle(
        "nodup",
        description="test",
        layers=[
            {"name": "layer_a", "source": "user", "content": "hello"},
            {"name": "layer_b", "source": "system", "content": "world"},
        ],
    )
    assert created.version == "v1"

    # 用相同内容更新(字段顺序不同)
    updated = await repo.update_bundle(
        "nodup",
        layers=[
            {"content": "hello", "name": "layer_a", "source": "user"},
            {"source": "system", "content": "world", "name": "layer_b"},
        ],
    )
    # 内容相同 → 不创建 v2,返回 v1
    assert updated.version == "v1"

    # 用不同内容更新
    updated2 = await repo.update_bundle(
        "nodup",
        layers=[
            {"name": "layer_a", "source": "user", "content": "changed"},
            {"name": "layer_b", "source": "system", "content": "world"},
        ],
    )
    # 内容不同 → 创建 v2
    assert updated2.version == "v2"

    # 验证版本数
    bundle = await repo.get_bundle("nodup")
    assert bundle is not None
    assert bundle.version_count == 2

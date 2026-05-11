from __future__ import annotations

from datetime import UTC, datetime

from chariot.memory.policy import MemoryPolicy
from chariot.repos.memory_repo import MemoryEntry


def _entry(entry_id: str, text: str, *, pinned: bool = False) -> MemoryEntry:
    now = datetime.now(tz=UTC)
    return MemoryEntry(
        id=entry_id,
        kind="preference",
        text=text,
        meta={},
        pinned=pinned,
        archived=False,
        created_at=now,
        updated_at=now,
    )


def test_memory_policy_selects_in_priority_order() -> None:
    policy = MemoryPolicy(max_items=4, max_chars=200)
    selected = policy.select(
        pinned=[_entry("pin", "Pinned note", pinned=True)],
        conversation=[_entry("conv", "Conversation note")],
        provider=[_entry("prov", "Provider note")],
        tags=[_entry("tag", "Tag note")],
    )

    assert [entry.id for entry in selected] == ["pin", "conv", "prov", "tag"]


def test_memory_policy_respects_limits() -> None:
    policy = MemoryPolicy(max_items=2, max_chars=20)
    selected = policy.select(
        pinned=[_entry("pin", "Very long pinned memory text")],
        conversation=[_entry("conv", "Conversation note")],
        provider=[_entry("prov", "Provider note")],
    )

    assert [entry.id for entry in selected] == ["pin"]
    assert policy.describe()["pinned_first"] is True

"""Sidecar tool service tests."""

from __future__ import annotations

import pytest

from chariot.agent.config import ToolEntry
from chariot.sidecar.services.tool import ToolService


class DummyRuntime:
    async def reload(self) -> None:
        return None


@pytest.fixture
def service() -> ToolService:
    return ToolService(DummyRuntime())


@pytest.fixture
def entry() -> ToolEntry:
    return ToolEntry(
        name="http_get",
        type="http_get",
        enabled=True,
        options={"allowed_domains": [], "max_bytes": 524288},
    )


@pytest.mark.asyncio
async def test_list_show_probe_tool_methods(service: ToolService, entry: ToolEntry, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_list_entries(self):  # type: ignore[no-untyped-def]
        return [entry]

    async def fake_get_entry(self, name: str):  # type: ignore[no-untyped-def]
        return entry if name == entry.name else None

    monkeypatch.setattr("chariot.repos.tool_repo.ToolRepo.list_entries", fake_list_entries)
    monkeypatch.setattr("chariot.repos.tool_repo.ToolRepo.get_entry", fake_get_entry)

    listed = await service.list_entries(object())
    assert listed[0]["name"] == "http_get"
    assert listed[0]["schema_"] is not None

    shown = await service.get_entry(object(), name="http_get")
    assert shown["name"] == "http_get"
    assert shown["schema_"] is not None

    probed = await service.probe_entry(object(), name="http_get")
    assert probed["ok"] is True
    assert probed["error"] is None


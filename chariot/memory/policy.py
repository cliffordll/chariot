"""Memory retrieval and injection policy."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class MemoryPolicy:
    version: str = "v1"
    name: str = "default_memory_policy"
    max_items: int = 6
    max_chars: int = 2400
    priority_order: tuple[str, ...] = ("pinned", "conversation", "provider", "tag")
    pinned_first: bool = True

    def describe(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "name": self.name,
            "max_items": self.max_items,
            "max_chars": self.max_chars,
            "priority_order": list(self.priority_order),
            "pinned_first": self.pinned_first,
        }

    def bounded(self, *, max_items: int | None = None) -> MemoryPolicy:
        limit = self.max_items if max_items is None else min(self.max_items, max_items)
        return replace(self, max_items=limit)

    def select(
        self,
        *,
        pinned: list[Any] | None = None,
        conversation: list[Any] | None = None,
        provider: list[Any] | None = None,
        tags: list[Any] | None = None,
    ) -> list[Any]:
        groups: dict[str, list[Any]] = {
            "pinned": pinned or [],
            "conversation": conversation or [],
            "provider": provider or [],
            "tag": tags or [],
        }
        ordered_sources = [source for source in self.priority_order if source in groups]
        for source in groups:
            if source not in ordered_sources:
                ordered_sources.append(source)
        if not self.pinned_first and "pinned" in ordered_sources:
            ordered_sources = [source for source in ordered_sources if source != "pinned"] + ["pinned"]

        selected: list[Any] = []
        seen: set[str] = set()
        total_chars = 0
        for source in ordered_sources:
            for entry in groups[source]:
                if entry.id in seen:
                    continue
                if len(selected) >= self.max_items:
                    return selected
                next_chars = total_chars + len(entry.text)
                if self.max_chars > 0 and selected and next_chars > self.max_chars:
                    return selected
                selected.append(entry)
                seen.add(entry.id)
                total_chars = next_chars
        return selected

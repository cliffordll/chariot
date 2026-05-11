"""Memory package exports with lazy imports to avoid repo/capture cycles."""

from __future__ import annotations

from typing import Any

__all__ = [
    "MemoryCaptureCandidate",
    "MemoryCaptureService",
    "MemoryEntry",
    "MemoryEventEntry",
    "MemoryLinkEntry",
    "MemoryPolicy",
    "MemoryRepo",
]


def __getattr__(name: str) -> Any:
    if name == "MemoryPolicy":
        from chariot.memory.policy import MemoryPolicy

        return MemoryPolicy
    if name in {"MemoryCaptureCandidate", "MemoryCaptureService"}:
        from chariot.memory.capture import MemoryCaptureCandidate, MemoryCaptureService

        return {
            "MemoryCaptureCandidate": MemoryCaptureCandidate,
            "MemoryCaptureService": MemoryCaptureService,
        }[name]
    if name in {"MemoryEntry", "MemoryEventEntry", "MemoryLinkEntry", "MemoryRepo"}:
        from chariot.repos.memory_repo import MemoryEntry, MemoryEventEntry, MemoryLinkEntry, MemoryRepo

        return {
            "MemoryEntry": MemoryEntry,
            "MemoryEventEntry": MemoryEventEntry,
            "MemoryLinkEntry": MemoryLinkEntry,
            "MemoryRepo": MemoryRepo,
        }[name]
    raise AttributeError(name)

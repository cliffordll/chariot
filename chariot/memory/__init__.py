"""Memory package exports with lazy imports to avoid repo/capture cycles."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Static-checker visibility for the names served lazily by __getattr__.
    from chariot.memory.capture import MemoryCaptureCandidate, MemoryCaptureService
    from chariot.memory.policy import MemoryPolicy
    from chariot.models.memory import MemoryEntry, MemoryEventEntry, MemoryLinkEntry
    from chariot.repos.memory_repo import MemoryRepo

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
    if name in {"MemoryEntry", "MemoryEventEntry", "MemoryLinkEntry"}:
        from chariot.models.memory import MemoryEntry, MemoryEventEntry, MemoryLinkEntry

        return {
            "MemoryEntry": MemoryEntry,
            "MemoryEventEntry": MemoryEventEntry,
            "MemoryLinkEntry": MemoryLinkEntry,
        }[name]
    if name == "MemoryRepo":
        from chariot.repos.memory_repo import MemoryRepo

        return MemoryRepo
    raise AttributeError(name)

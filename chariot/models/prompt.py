"""Prompt domain model.

Business dataclasses for prompt bundles, versions, traces, layers, and the
snapshot composed from a ChatRequest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class PromptBundleEntry:
    id: str
    name: str
    description: str | None
    layers: list[dict[str, Any]]
    is_active: bool
    version_count: int
    active_version: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class PromptVersionEntry:
    id: str
    bundle_id: str
    bundle_name: str
    version: str
    spec: dict[str, Any]
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class PromptTraceEntry:
    id: str
    bundle_id: str
    bundle_name: str
    version_id: str
    version: str
    conversation_id: str | None
    provider_name: str
    model: str | None
    request: dict[str, Any]
    source_refs: list[dict[str, Any]]
    prompt_size: int
    created_at: datetime


@dataclass(frozen=True)
class PromptLayer:
    name: str
    source: str
    content: Any | None = None


@dataclass(frozen=True)
class PromptSnapshot:
    bundle_name: str
    version: str
    provider_name: str
    model: str | None
    conversation_id: str | None
    request: dict[str, Any]
    layers: list[PromptLayer]
    source_refs: list[dict[str, Any]]
    prompt_size: int

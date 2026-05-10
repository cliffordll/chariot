"""Prompt snapshot composition helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any

from chariot.agent.chat_request import ChatRequest


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


def build_snapshot(
    req: ChatRequest,
    *,
    provider_name: str,
    model: str | None,
    bundle_name: str,
    version: str,
    memory_entries: list[dict[str, Any]] | None = None,
    memory_policy: dict[str, Any] | None = None,
) -> PromptSnapshot:
    request = asdict(req)
    layers = _build_layers(
        req,
        provider_name=provider_name,
        model=model,
        memory_entries=memory_entries,
        memory_policy=memory_policy,
    )
    source_refs = [
        {
            "layer": layer.name,
            "source": layer.source,
            "present": layer.content is not None,
        }
        for layer in layers
    ]
    prompt_size = len(json.dumps(request, ensure_ascii=False, sort_keys=True))
    return PromptSnapshot(
        bundle_name=bundle_name,
        version=version,
        provider_name=provider_name,
        model=model,
        conversation_id=req.conversation_id,
        request=request,
        layers=layers,
        source_refs=source_refs,
        prompt_size=prompt_size,
    )


def _build_layers(
    req: ChatRequest,
    *,
    provider_name: str,
    model: str | None,
    memory_entries: list[dict[str, Any]] | None,
    memory_policy: dict[str, Any] | None,
) -> list[PromptLayer]:
    tool_names = [tool.name for tool in req.tools] if req.tools is not None else None
    return [
        PromptLayer(name="base_system", source="ChatRequest.system", content=req.system),
        PromptLayer(name="developer", source="runtime default", content=None),
        PromptLayer(
            name="runtime",
            source="AIAgent runtime context",
            content={
                "provider_name": provider_name,
                "model": model,
                "conversation_id": req.conversation_id,
                "agent_id": req.agent_id,
            },
        ),
        PromptLayer(
            name="memory",
            source="MemoryRepo.list_relevant_entries",
            content={
                "policy": memory_policy or {"version": "v1", "name": "default_memory_policy"},
                "entries": memory_entries,
            },
        ),
        PromptLayer(name="skill", source="not implemented yet", content=None),
        PromptLayer(name="tool_instruction", source="ChatRequest.tools", content=tool_names),
        PromptLayer(name="tool_choice", source="ChatRequest.tool_choice", content=req.tool_choice),
        PromptLayer(name="thinking", source="ChatRequest.thinking", content=req.thinking),
    ]

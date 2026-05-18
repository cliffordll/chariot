"""Prompt snapshot composition.

`PromptComposer` is a stateless utility class. All public methods are
@staticmethod; the class exists to give a clear home for prompt-side
composition logic and to keep this module free of module-level free
functions (per CLAUDE.md ⭐).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from chariot.agent.chat_request import ChatRequest
from chariot.models.prompt import PromptLayer, PromptSnapshot


class PromptComposer:
    """Stateless prompt composition utilities."""

    @staticmethod
    def build_snapshot(
        req: ChatRequest,
        *,
        provider_id: str | None = None,
        provider_snapshot: str,
        model: str | None,
        bundle_name: str,
        version: str,
        memory_entries: list[dict[str, Any]] | None = None,
        memory_policy: dict[str, Any] | None = None,
    ) -> PromptSnapshot:
        request = asdict(req)
        layers = PromptComposer._build_layers(
            req,
            provider_snapshot=provider_snapshot,
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
            provider_snapshot=provider_snapshot,
            model=model,
            conversation_id=req.conversation_id,
            request=request,
            layers=layers,
            source_refs=source_refs,
            prompt_size=prompt_size,
            provider_id=provider_id,
        )

    @staticmethod
    def _build_layers(
        req: ChatRequest,
        *,
        provider_snapshot: str,
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
                    "provider_snapshot": provider_snapshot,
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

    @staticmethod
    def render_layers_text(
        layers: list[dict[str, Any]],
        *,
        existing_system: str | None = None,
        memory_entries: list[dict[str, Any]] | None = None,
        memory_policy: dict[str, Any] | None = None,
    ) -> str | None:
        parts: list[str] = []
        for layer in layers:
            content = layer.get("content")
            if layer.get("name") == "memory" and memory_entries is not None:
                content = {
                    "policy": memory_policy or {"version": "v1", "name": "default_memory_policy"},
                    "entries": memory_entries,
                }
            if content is None:
                continue
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False)
            parts.append(f"[{layer.get('name', 'layer')}]\n{content}")
        if existing_system:
            parts.append(f"[request.system]\n{existing_system}")
        if not parts:
            return existing_system
        return "\n\n".join(parts)

"""Automatic memory capture helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from chariot.agent.chat_request import ChatRequest
from chariot.memory.policy import MemoryPolicy
from chariot.repos.memory_repo import MemoryEntry, MemoryRepo

_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?；;\n]+")
_PREFERENCE_HINTS = (
    "默认",
    "优先",
    "尽量",
    "保持",
    "总是",
    "always",
    "prefer",
    "remember",
    "记住",
)
_INSTRUCTION_HINTS = (
    "不要",
    "必须",
    "请用",
    "请保持",
    "以后",
    "之后",
    "never",
    "must",
)


@dataclass(frozen=True)
class MemoryCaptureCandidate:
    kind: str
    text: str
    meta: dict[str, Any]
    links: list[dict[str, Any]]


class MemoryCaptureService:
    """Extract small reusable memory items from turns and errors."""

    def __init__(self, repo: MemoryRepo) -> None:
        self._repo = repo

    async def capture_turn(
        self,
        *,
        req: ChatRequest,
        provider_name: str,
        policy: MemoryPolicy,
        prompt_trace_id: str | None = None,
        context_trace_id: str | None = None,
    ) -> list[MemoryEntry]:
        candidates = self.extract_from_request(
            req,
            provider_name=provider_name,
            prompt_trace_id=prompt_trace_id,
            context_trace_id=context_trace_id,
        )
        if policy.max_items > 0:
            candidates = candidates[: policy.max_items]
        return await self._persist_candidates(candidates)

    async def capture_error(
        self,
        *,
        conversation_id: str | None,
        provider_name: str,
        error_type: str,
        error_message: str,
        prompt_trace_id: str | None = None,
        context_trace_id: str | None = None,
    ) -> list[MemoryEntry]:
        candidate = self._error_candidate(
            conversation_id=conversation_id,
            provider_name=provider_name,
            error_type=error_type,
            error_message=error_message,
            prompt_trace_id=prompt_trace_id,
            context_trace_id=context_trace_id,
        )
        if candidate is None:
            return []
        return await self._persist_candidates([candidate])

    def extract_from_request(
        self,
        req: ChatRequest,
        *,
        provider_name: str,
        prompt_trace_id: str | None = None,
        context_trace_id: str | None = None,
    ) -> list[MemoryCaptureCandidate]:
        candidates: list[MemoryCaptureCandidate] = []
        for message in req.messages:
            if message.role != "user":
                continue
            for sentence in self._split_sentences(self._message_text(message.content)):
                candidate = self._sentence_candidate(
                    sentence,
                    provider_name=provider_name,
                    conversation_id=req.conversation_id,
                    prompt_trace_id=prompt_trace_id,
                    context_trace_id=context_trace_id,
                )
                if candidate is not None:
                    candidates.append(candidate)
        return candidates

    def _sentence_candidate(
        self,
        sentence: str,
        *,
        provider_name: str,
        conversation_id: str | None,
        prompt_trace_id: str | None,
        context_trace_id: str | None,
    ) -> MemoryCaptureCandidate | None:
        text = sentence.strip()
        if len(text) < 6 or len(text) > 120:
            return None
        lower = text.lower()
        has_preference_hint = any(hint in text or hint in lower for hint in _PREFERENCE_HINTS)
        has_instruction_hint = any(hint in text or hint in lower for hint in _INSTRUCTION_HINTS)
        if not has_preference_hint and not has_instruction_hint:
            return None
        kind = "preference" if has_preference_hint else "instruction"
        meta: dict[str, Any] = {
            "capture_source": "conversation",
            "provider_name": provider_name,
            "conversation_id": conversation_id,
        }
        if prompt_trace_id is not None:
            meta["prompt_trace_id"] = prompt_trace_id
        if context_trace_id is not None:
            meta["context_trace_id"] = context_trace_id
        links = [{"link_type": "provider", "link_value": provider_name}]
        if conversation_id is not None:
            links.append({"link_type": "conversation", "link_value": conversation_id})
        if prompt_trace_id is not None:
            links.append({"link_type": "trace", "link_value": prompt_trace_id})
        if context_trace_id is not None:
            links.append({"link_type": "context_trace", "link_value": context_trace_id})
        links.append({"link_type": "tag", "link_value": "auto"})
        return MemoryCaptureCandidate(kind=kind, text=text, meta=meta, links=links)

    def _error_candidate(
        self,
        *,
        conversation_id: str | None,
        provider_name: str,
        error_type: str,
        error_message: str,
        prompt_trace_id: str | None,
        context_trace_id: str | None,
    ) -> MemoryCaptureCandidate | None:
        if not error_type:
            return None
        normalized = error_message.strip()
        if not normalized:
            return None
        text = f"When {error_type} happens, check: {normalized}"
        if len(text) > 120:
            text = text[:117].rstrip() + "..."
        meta: dict[str, Any] = {
            "capture_source": "error",
            "error_type": error_type,
            "provider_name": provider_name,
            "conversation_id": conversation_id,
        }
        if prompt_trace_id is not None:
            meta["prompt_trace_id"] = prompt_trace_id
        if context_trace_id is not None:
            meta["context_trace_id"] = context_trace_id
        links = [
            {"link_type": "provider", "link_value": provider_name},
            {"link_type": "tag", "link_value": "auto"},
        ]
        if conversation_id is not None:
            links.append({"link_type": "conversation", "link_value": conversation_id})
        if prompt_trace_id is not None:
            links.append({"link_type": "trace", "link_value": prompt_trace_id})
        if context_trace_id is not None:
            links.append({"link_type": "context_trace", "link_value": context_trace_id})
        return MemoryCaptureCandidate(kind="lesson", text=text, meta=meta, links=links)

    async def _persist_candidates(
        self,
        candidates: list[MemoryCaptureCandidate],
    ) -> list[MemoryEntry]:
        captured: list[MemoryEntry] = []
        for candidate in candidates:
            existing = await self._find_exact(candidate.kind, candidate.text)
            if existing is not None:
                captured.append(existing)
                continue
            entry = await self._repo.create(
                kind=candidate.kind,
                text=candidate.text,
                meta=candidate.meta,
                links=candidate.links,
            )
            captured.append(entry)
        return captured

    async def _find_exact(self, kind: str, text: str) -> MemoryEntry | None:
        entries = await self._repo.search_entries(text, limit=20)
        for entry in entries:
            if entry.kind == kind and entry.text == text:
                return entry
        return None

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        parts = [part.strip() for part in _SENTENCE_SPLIT_RE.split(text) if part.strip()]
        return parts or ([text.strip()] if text.strip() else [])

    @staticmethod
    def _message_text(content: str | list[dict[str, Any]]) -> str:
        if isinstance(content, str):
            return content
        texts: list[str] = []
        for block in content:
            block_type = str(block.get("type") or "")
            if block_type == "text" and isinstance(block.get("text"), str):
                texts.append(block["text"])
        return "\n".join(texts)

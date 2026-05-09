"""Provider contract helpers for Phase 1 boundary tightening."""

from __future__ import annotations

import dataclasses

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest
from chariot.providers.base import BaseProviderCapabilities


class ProviderContractError(ValueError):
    """Provider emitted an invalid event sequence."""


def normalize_request(
    req: ChatRequest, capabilities: BaseProviderCapabilities
) -> ChatRequest:
    """Drop request fields unsupported by the selected provider."""

    updates: dict[str, object | None] = {}

    if not capabilities.supports_system and req.system is not None:
        updates["system"] = None

    if not capabilities.supports_tools:
        if req.tools is not None:
            updates["tools"] = None
        if req.tool_choice is not None:
            updates["tool_choice"] = None
    elif not capabilities.supports_tool_choice and req.tool_choice is not None:
        updates["tool_choice"] = None

    if not capabilities.supports_thinking and req.thinking is not None:
        updates["thinking"] = None

    if not updates:
        return req
    return dataclasses.replace(req, **updates)


class ProviderEventValidator:
    """Validate one provider turn before AgentLoop consumes the events."""

    def __init__(self) -> None:
        self._saw_message_start = False
        self._saw_message_stop = False
        self._open_blocks: set[int] = set()

    def accept(self, event: ChatEvent) -> ChatEvent:
        kind = event.kind

        if kind == "ping":
            return event
        if kind == "error":
            return event

        if kind == "message_start":
            if self._saw_message_start:
                raise ProviderContractError("duplicate message_start in one provider turn")
            if event.message is None:
                raise ProviderContractError("message_start missing message payload")
            self._saw_message_start = True
            return event

        if not self._saw_message_start:
            raise ProviderContractError(f"{kind} arrived before message_start")
        if self._saw_message_stop:
            raise ProviderContractError(f"{kind} arrived after message_stop")

        if kind == "content_block_start":
            if event.index is None:
                raise ProviderContractError("content_block_start missing index")
            if event.content_block is None:
                raise ProviderContractError("content_block_start missing content_block")
            if event.index in self._open_blocks:
                raise ProviderContractError(f"duplicate open content block index {event.index}")
            self._open_blocks.add(event.index)
            return event

        if kind == "content_block_delta":
            if event.index is None:
                raise ProviderContractError("content_block_delta missing index")
            if event.delta is None:
                raise ProviderContractError("content_block_delta missing delta")
            if event.index not in self._open_blocks:
                raise ProviderContractError(
                    f"content_block_delta for unopened index {event.index}"
                )
            return event

        if kind == "content_block_stop":
            if event.index is None:
                raise ProviderContractError("content_block_stop missing index")
            if event.index not in self._open_blocks:
                raise ProviderContractError(
                    f"content_block_stop for unopened index {event.index}"
                )
            self._open_blocks.remove(event.index)
            return event

        if kind == "message_delta":
            if event.delta is None:
                raise ProviderContractError("message_delta missing delta")
            return event

        if kind == "message_stop":
            if self._open_blocks:
                raise ProviderContractError("message_stop arrived before content blocks closed")
            self._saw_message_stop = True
            return event

        return event

    def ensure_complete(self) -> None:
        if not self._saw_message_start:
            raise ProviderContractError("provider turn ended without message_start")
        if not self._saw_message_stop:
            raise ProviderContractError("provider turn ended without message_stop")

"""AgentLoop: provider stream forwarding + tool loop orchestration."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest, Message
from chariot.agent.exceptions import ProviderError
from chariot.agent.provider_contract import ProviderContractError, ProviderEventValidator
from chariot.agent.tool_execution import ToolExecutionService

if TYPE_CHECKING:
    from chariot.providers.base import BaseProvider
    from chariot.repos.conversation_repo import ConversationRepo
    from chariot.tools.base import BaseTool


_DEFAULT_MAX_ITER = 10


class AgentLoop:
    """Consume provider events, execute tool calls, and build the next turn."""

    def __init__(
        self,
        *,
        provider: BaseProvider,
        tools: dict[str, BaseTool],
        repo: ConversationRepo | None,
        conversation_id: str | None,
        max_iter: int = _DEFAULT_MAX_ITER,
    ) -> None:
        self._provider = provider
        self._tools = tools
        self._tool_execution = ToolExecutionService(tools)
        self._repo = repo
        self._conversation_id = conversation_id
        self._max_iter = max_iter

    async def stream_chat(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        current_req = req
        for _attempt in range(self._max_iter):
            assistant_blocks: list[dict[str, Any]] = []
            tool_use_inputs: dict[int, str] = {}
            stop_reason: str | None = None
            saw_error = False
            validator = ProviderEventValidator()

            try:
                async for event in self._provider.generate(current_req):
                    validator.accept(event)
                    yield event
                    self._buffer_event(assistant_blocks, tool_use_inputs, event)
                    if event.kind == "message_delta":
                        stop_reason = self._extract_stop_reason(event.delta)
                    if event.kind == "error":
                        saw_error = True
                        return
                validator.ensure_complete()
            except ProviderContractError as e:
                yield ChatEvent.error_event(
                    error_type="invalid_provider_event",
                    error_message=str(e),
                )
                return
            except ProviderError as e:
                yield ChatEvent.error_event(error_type=e.code, error_message=e.message)
                return

            if saw_error:
                return

            await self._persist_assistant(assistant_blocks)

            if stop_reason == "tool_use":
                tool_results = await self._execute_tool_calls(assistant_blocks)
                for tr_event in tool_results:
                    yield tr_event
                await self._persist_tool_results(tool_results)
                current_req = self._build_next_req(current_req, assistant_blocks, tool_results)
                continue

            yield ChatEvent.stream_done_event()
            return

        yield ChatEvent.error_event(
            error_type="agent_iter_exceeded",
            error_message=f"tool loop exceeded {self._max_iter} iterations",
        )

    @staticmethod
    def _buffer_event(
        blocks: list[dict[str, Any]],
        tool_use_inputs: dict[int, str],
        event: ChatEvent,
    ) -> None:
        if event.kind == "content_block_start":
            block_data = dict(event.content_block or {})
            blocks.append({"_index": event.index, "block": block_data})
            return

        if event.kind == "content_block_delta":
            delta = event.delta or {}
            delta_type = delta.get("type")
            if delta_type == "text_delta":
                text = delta.get("text", "")
                target = AgentLoop._find_block_by_index(blocks, event.index)
                if target is not None and target.get("type") == "text":
                    target["text"] = target.get("text", "") + text
            elif delta_type == "input_json_delta" and event.index is not None:
                tool_use_inputs.setdefault(event.index, "")
                tool_use_inputs[event.index] += delta.get("partial_json", "")
            return

        if event.kind == "content_block_stop" and event.index is not None:
            target = AgentLoop._find_block_by_index(blocks, event.index)
            if target is None or target.get("type") != "tool_use":
                tool_use_inputs.pop(event.index, None)
                return
            json_str = tool_use_inputs.pop(event.index, "")
            try:
                target["input"] = json.loads(json_str) if json_str else {}
            except json.JSONDecodeError:
                target["input"] = {"_invalid_input_json": json_str}

    @staticmethod
    def _find_block_by_index(
        blocks: list[dict[str, Any]], index: int | None
    ) -> dict[str, Any] | None:
        if index is None:
            return None
        for entry in blocks:
            if entry.get("_index") == index:
                return entry["block"]
        return None

    @staticmethod
    def _extract_stop_reason(delta: dict[str, Any] | None) -> str | None:
        if not delta:
            return None
        sr = delta.get("stop_reason")
        return sr if isinstance(sr, str) else None

    async def _persist_assistant(self, assistant_blocks: list[dict[str, Any]]) -> None:
        if self._repo is None or self._conversation_id is None:
            return
        if not assistant_blocks:
            return
        content = [entry["block"] for entry in assistant_blocks]
        await self._repo.append_message(
            self._conversation_id,
            role="assistant",
            content=content,
            provider_name=self._provider.config.name,
        )

    async def _persist_tool_results(self, tool_results: list[ChatEvent]) -> None:
        if self._repo is None or self._conversation_id is None:
            return
        if not tool_results:
            return
        content = [self._tool_result_event_to_block(ev) for ev in tool_results]
        await self._repo.append_message(
            self._conversation_id,
            role="user",
            content=content,
        )

    @staticmethod
    def _tool_result_event_to_block(ev: ChatEvent) -> dict[str, Any]:
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": ev.tool_use_id,
            "content": ev.content,
        }
        if ev.is_error:
            block["is_error"] = True
        return block

    async def _execute_tool_calls(
        self, assistant_blocks: list[dict[str, Any]]
    ) -> list[ChatEvent]:
        results: list[ChatEvent] = []
        for entry in assistant_blocks:
            block = entry["block"]
            if block.get("type") != "tool_use":
                continue
            results.append(await self._execute_tool_call(block))
        return results

    async def _execute_tool_call(self, tool_use_block: dict[str, Any]) -> ChatEvent:
        return await self._tool_execution.execute_tool_call(
            tool_use_id=str(tool_use_block.get("id", "")),
            tool_name=str(tool_use_block.get("name", "")),
            tool_input=tool_use_block.get("input", {}),
        )

    @staticmethod
    def _build_next_req(
        prev_req: ChatRequest,
        assistant_blocks: list[dict[str, Any]],
        tool_results: list[ChatEvent],
    ) -> ChatRequest:
        new_messages = list(prev_req.messages)
        new_messages.append(
            Message(
                role="assistant",
                content=[entry["block"] for entry in assistant_blocks],
            )
        )
        tool_result_blocks = [AgentLoop._tool_result_event_to_block(ev) for ev in tool_results]
        new_messages.append(Message(role="user", content=tool_result_blocks))
        return dataclasses.replace(prev_req, messages=new_messages)

"""AgentLoop: provider stream forwarding + tool loop orchestration."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest, Message
from chariot.agent.exceptions import ProviderError
from chariot.providers.contract import ProviderContractError, ProviderEventValidator
from chariot.tools.execution import ToolExecutionService

if TYPE_CHECKING:
    from chariot.audit import AuditHookManager
    from chariot.guardrails import GuardrailEngine
    from chariot.guardrails.approval import ApprovalPolicy
    from chariot.providers.base import BaseProvider
    from chariot.services.conversation import ConversationMessageStore
    from chariot.tools.base import BaseTool
    from chariot.trace import TurnHandle


_DEFAULT_MAX_ITER = 10


class AgentLoop:
    """Consume provider events, execute tool calls, and build the next turn."""

    def __init__(
        self,
        *,
        provider: BaseProvider,
        tools: dict[str, BaseTool],
        message_store: ConversationMessageStore | None,
        conversation_id: str | None,
        max_iter: int = _DEFAULT_MAX_ITER,
        turn: TurnHandle | None = None,
        guardrail_engine: GuardrailEngine | None = None,
        approval_policy: ApprovalPolicy | None = None,
        audit_hooks: AuditHookManager | None = None,
        todo_store: Any | None = None,
        agent_profile: str | None = None,
        provider_snapshot: str | None = None,
    ) -> None:
        self._provider = provider
        self._tools = tools
        self._tool_execution = ToolExecutionService(
            tools,
            guardrail_engine=guardrail_engine,
            approval_policy=approval_policy,
            audit_hooks=audit_hooks,
            todo_store=todo_store,
        )
        self._message_store = message_store
        self._conversation_id = conversation_id
        self._max_iter = max_iter
        self._turn = turn
        self._agent_profile = agent_profile
        self._provider_snapshot = provider_snapshot  # provider 展示快照,用于持久化 last_provider;None = 不记录

    async def stream_chat(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        current_req = req
        for _attempt in range(self._max_iter):
            assistant_blocks: list[dict[str, Any]] = []
            tool_use_inputs: dict[int, str] = {}
            stop_reason: str | None = None
            saw_error = False
            error_type: str | None = None
            last_usage: dict[str, Any] | None = None
            validator = ProviderEventValidator()

            # Phase B1:provider call trace 配对(turn=None 时退化为 no-op)
            pc_handle = (
                self._turn.begin_provider_call(
                    provider_snapshot=self._provider_snapshot or self._provider.config.name,
                    model=self._provider.config.model,
                )
                if self._turn is not None
                else None
            )

            try:
                async for event in self._provider.generate(current_req):
                    validator.accept(event)
                    yield event
                    self._buffer_event(assistant_blocks, tool_use_inputs, event)
                    if event.kind == "message_delta":
                        stop_reason = self._extract_stop_reason(event.delta)
                    if event.usage:
                        last_usage = event.usage
                    if event.kind == "error":
                        saw_error = True
                        error_type = event.error_type
                        if pc_handle is not None:
                            await pc_handle.finish(error_type=event.error_type)
                        return
                validator.ensure_complete()
            except ProviderContractError as e:
                if pc_handle is not None:
                    await pc_handle.finish(error_type="invalid_provider_event")
                yield ChatEvent.error_event(
                    error_type="invalid_provider_event",
                    error_message=str(e),
                )
                return
            except ProviderError as e:
                if pc_handle is not None:
                    await pc_handle.finish(error_type=e.code)
                yield ChatEvent.error_event(error_type=e.code, error_message=e.message)
                return

            # 正常或 stop_reason 结束 → 写 provider call 完成摘要
            if pc_handle is not None and not saw_error:
                await pc_handle.finish(
                    response_summary={
                        "stop_reason": stop_reason,
                        "usage": last_usage or {},
                    },
                    error_type=error_type,
                )

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
    def _find_block_by_index(blocks: list[dict[str, Any]], index: int | None) -> dict[str, Any] | None:
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
        if self._message_store is None or self._conversation_id is None:
            return
        if not assistant_blocks:
            return
        content = [entry["block"] for entry in assistant_blocks]
        await self._message_store.append_assistant_message(
            self._conversation_id,
            content=content,
            provider_snapshot=self._provider_snapshot,
            agent_profile=self._agent_profile,
        )

    async def _persist_tool_results(self, tool_results: list[ChatEvent]) -> None:
        if self._message_store is None or self._conversation_id is None:
            return
        if not tool_results:
            return
        content = [self._tool_result_event_to_block(ev) for ev in tool_results]
        await self._message_store.append_tool_result_message(
            self._conversation_id,
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

    async def _execute_tool_calls(self, assistant_blocks: list[dict[str, Any]]) -> list[ChatEvent]:
        results: list[ChatEvent] = []
        for entry in assistant_blocks:
            block = entry["block"]
            if block.get("type") != "tool_use":
                continue
            results.append(await self._execute_tool_call(block))
        return results

    async def _execute_tool_call(self, tool_use_block: dict[str, Any]) -> ChatEvent:
        tool_name = str(tool_use_block.get("name", ""))
        tool_input = tool_use_block.get("input", {})
        args = tool_input if isinstance(tool_input, dict) else {}
        tc_handle = self._turn.begin_tool_call(tool_name=tool_name, arguments=args) if self._turn is not None else None
        result = await self._tool_execution.execute_tool_call(
            tool_use_id=str(tool_use_block.get("id", "")),
            tool_name=tool_name,
            tool_input=tool_input,
        )
        if tc_handle is not None:
            from chariot.models.trace import ToolCallStatus

            status = ToolCallStatus.ERROR if result.is_error else ToolCallStatus.OK
            # result_summary 取 content 前 N 字摘要,避免大 result 灌进 trace 表
            summary = self._summarize_tool_result(result)
            error_message = summary.get("error") if status == ToolCallStatus.ERROR else None
            await tc_handle.finish(status=status, result_summary=summary, error_message=error_message)
        return result

    @staticmethod
    def _summarize_tool_result(result: ChatEvent) -> dict[str, Any]:
        """tool_result event → 摘要 dict(用于 trace_tool_calls.result_summary)。

        只保留前 N 字符的 content + is_error;完整 content 仍在 conversation
        messages 表里(stateful)/ event 流里(stateless)。
        """
        snippet_chars = 512
        content = result.content
        if isinstance(content, list):
            text_parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(str(block.get("text", "")))
            text = "".join(text_parts)
        else:
            text = str(content or "")
        snippet = text[:snippet_chars]
        truncated = len(text) > snippet_chars
        out: dict[str, Any] = {"is_error": result.is_error, "snippet": snippet}
        if truncated:
            out["truncated"] = True
        if result.is_error:
            out["error"] = snippet
        return out

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

"""AgentLoop — chat 主循环(0.6.0,替代 0.5.0 server/agent.py 里的工具循环)。

替代 0.5.0 的 `_stream_tool_loop_body` + `_TurnState` + `_process_one_turn` +
`_emit_synthetic_tool_result_message` 这一坨。**单一职责**:消费 Provider
events + 检测 tool_use + 跑工具 + 注入 tool_result events + 续轮。

详见 `docs/DESIGN.md` §6.2 / §6.4。

主流程(每轮):

1. `provider.generate(req)` → 实时透传给 caller
2. 内部 buffer assistant content blocks(从 content_block_start /
   content_block_delta+ / content_block_stop 累积重组)
3. `message_delta` 收 stop_reason
4. `message_stop` 之后:
   - 持久化 assistant content(若有 repo + convo_id)
   - stop_reason == "tool_use" → 跑工具 → yield tool_result events
     (chariot 注入)→ 拼下轮 req → 续轮
   - 其它(end_turn / max_tokens / stop_sequence)→ yield stream_done → break
5. `max_iter` 超 → yield error(error_type="agent_iter_exceeded")

关键约定:
- **不再做 SSE 解析**(Provider 已吐 typed events)
- **不再合成 SSE 帧**(`tool_result` 直接是 `ChatEvent`,surface 自己序列化)
- **错误传播**:工具抛 → `tool_result(is_error=True)`;Provider 抛 / yield
  `kind=error` → AgentLoop 直接 return(流终结)
- 持久化时机:`message_stop` 之后**整轮一次性**(不在 token 级写库)

模块级零自由函数(CLAUDE.md ⭐)。
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, cast

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest, Message
from chariot.agent.exceptions import ProviderError
from chariot.agent.provider_contract import ProviderContractError, ProviderEventValidator

if TYPE_CHECKING:
    from chariot.providers.base import BaseProvider
    from chariot.repos.convo_repo import ConvoRepo
    from chariot.tools.base import BaseTool


_DEFAULT_MAX_ITER = 10


class AgentLoop:
    """chat 主循环 —— 单轮 Provider stream + 多轮工具调用 + 持久化。

    由 `AIAgent.run_chat` 实例化并 invoke;调用方通常不直接构造。所有状态在实例
    字段里(provider / tools / repo / convo_id / max_iter),`stream_chat` 是唯一对外
    方法。
    """

    def __init__(
        self,
        *,
        provider: BaseProvider,
        tools: dict[str, BaseTool],
        repo: ConvoRepo | None,
        convo_id: str | None,
        max_iter: int = _DEFAULT_MAX_ITER,
    ) -> None:
        self._provider = provider
        self._tools = tools
        self._repo = repo
        self._convo_id = convo_id
        self._max_iter = max_iter

    async def stream_chat(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        """主入口。逐 ChatEvent yield;退出条件见模块 docstring。"""
        current_req = req
        for _attempt in range(self._max_iter):
            assistant_blocks: list[dict[str, Any]] = []
            tool_use_inputs: dict[int, str] = {}  # index → 累计的 input_json 字符串
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
                # 200 前抛的 ProviderError(契约见 DESIGN §6.4.2):转 error event
                # yield 给 surface,流终结。200 后的错由 Provider 自己 yield error
                # event(走上面 saw_error 分支),不会到这。
                yield ChatEvent.error_event(error_type=e.code, error_message=e.message)
                return

            if saw_error:
                return  # provider 已 yield error,流终结

            # message_stop 之后:持久化 assistant content
            await self._persist_assistant(assistant_blocks)

            if stop_reason == "tool_use":
                tool_results = await self._execute_tools(assistant_blocks)
                for tr_event in tool_results:
                    yield tr_event
                await self._persist_tool_results(tool_results)
                current_req = self._build_next_req(current_req, assistant_blocks, tool_results)
                continue

            # end_turn / max_tokens / stop_sequence / 缺失:正常收尾
            yield ChatEvent.stream_done_event()
            return

        # 超 max_iter
        yield ChatEvent.error_event(
            error_type="agent_iter_exceeded",
            error_message=f"工具循环超过 {self._max_iter} 轮上限",
        )

    # ---- buffer:从流式 events 累积 assistant content blocks ----

    @staticmethod
    def _buffer_event(
        blocks: list[dict[str, Any]],
        tool_use_inputs: dict[int, str],
        event: ChatEvent,
    ) -> None:
        """从 ChatEvent 重组 assistant content block 数组。

        累积规则:
        - `content_block_start` → blocks 末尾 append 一个 block(text / tool_use)
        - `content_block_delta(text_delta)` → 拼到 last block 的 `text` 字段
        - `content_block_delta(input_json_delta)` → 累积到 `tool_use_inputs[index]`
        - `content_block_stop` → tool_use 完整时把累积 JSON 字符串 parse 进 block.input
        """
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
                # parse 失败 → 标记 invalid,留给 _execute_tools 转
                # tool_result(is_error=True) 表达
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

    # ---- 持久化 ----

    async def _persist_assistant(self, assistant_blocks: list[dict[str, Any]]) -> None:
        """整轮 assistant 落库(message_stop 之后)。

        重组成纯 content blocks 数组(去 _index 元数据)写入 messages 表;
        `provider_name` 字段记本轮用的 entry name(provider.config.name)。
        """
        if self._repo is None or self._convo_id is None:
            return
        if not assistant_blocks:
            return
        content = [entry["block"] for entry in assistant_blocks]
        await self._repo.append_message(
            self._convo_id,
            role="assistant",
            content=content,
            provider_name=self._provider.config.name,
        )

    async def _persist_tool_results(self, tool_results: list[ChatEvent]) -> None:
        """tool_result events 整体作为 user message 落库(对应 Claude 协议)。"""
        if self._repo is None or self._convo_id is None:
            return
        if not tool_results:
            return
        content = [self._tool_result_event_to_block(ev) for ev in tool_results]
        await self._repo.append_message(
            self._convo_id,
            role="user",
            content=content,
        )

    @staticmethod
    def _tool_result_event_to_block(ev: ChatEvent) -> dict[str, Any]:
        """ChatEvent(tool_result) → Claude tool_result content block。"""
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": ev.tool_use_id,
            "content": ev.content,
        }
        if ev.is_error:
            block["is_error"] = True
        return block

    # ---- 跑工具 ----

    async def _execute_tools(self, assistant_blocks: list[dict[str, Any]]) -> list[ChatEvent]:
        """对 assistant content blocks 里的所有 tool_use 跑对应 Tool.execute,
        返一组 tool_result ChatEvent。
        """
        results: list[ChatEvent] = []
        for entry in assistant_blocks:
            block = entry["block"]
            if block.get("type") != "tool_use":
                continue
            tool_use_id = block.get("id", "")
            tool_name = block.get("name", "")
            tool_input = block.get("input", {})
            results.append(await self._execute_one_tool(tool_use_id, tool_name, tool_input))
        return results

    async def _execute_one_tool(
        self,
        tool_use_id: str,
        tool_name: str,
        tool_input: Any,
    ) -> ChatEvent:
        """单个工具执行 + 错误兜底。"""
        # input parse 失败检测(_buffer 时标的)
        if isinstance(tool_input, dict) and "_invalid_input_json" in tool_input:
            return ChatEvent.tool_result_event(
                tool_use_id=tool_use_id,
                content=f"invalid_json from LLM: {tool_input['_invalid_input_json']!r}",
                is_error=True,
            )

        tool = self._tools.get(tool_name)
        if tool is None:
            return ChatEvent.tool_result_event(
                tool_use_id=tool_use_id,
                content=f"unknown tool: {tool_name!r}",
                is_error=True,
            )

        safe_input: dict[str, Any] = (
            cast(dict[str, Any], tool_input) if isinstance(tool_input, dict) else {}
        )
        try:
            result_block = await tool.execute(safe_input)
        except Exception as e:
            return ChatEvent.tool_result_event(
                tool_use_id=tool_use_id,
                content=f"tool {tool_name!r} 执行异常: {e}",
                is_error=True,
            )

        return ChatEvent.tool_result_event(
            tool_use_id=tool_use_id,
            content=result_block.get("content", []),
            is_error=bool(result_block.get("is_error", False)),
        )

    # ---- 拼下一轮 req ----

    @staticmethod
    def _build_next_req(
        prev_req: ChatRequest,
        assistant_blocks: list[dict[str, Any]],
        tool_results: list[ChatEvent],
    ) -> ChatRequest:
        """拼下一轮 ChatRequest:
        - 加 1 条 assistant message(本轮 content blocks)
        - 加 1 条 user message(本轮 tool_result blocks)
        其余字段不变(model / system / tools / sampling 等沿用)。
        """
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

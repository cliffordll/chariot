"""Context snapshot 构造逻辑(被动记录)。

`ContextComposer` 把 `ChatRequest` + 运行时状态 + memory entries 组装成
`ContextSnapshot`(7 个 `ContextSlice`)。无状态工具类,只暴露 `build_snapshot`
classmethod;dataclass 形态在 `chariot/models/context.py`。

**职责边界**:Composer **只记录**(给 B1 trace / audit / RL 回放快照),**不改
request**。要在 provider 收到 request 前主动改写 messages(摘要 / pruning)的
逻辑在 `ContextCompressor`(`compressor.py`)。
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from chariot.agent.chat_request import ChatRequest
from chariot.models.context import ContextSlice, ContextSnapshot


class ContextComposer:
    """组装一次请求的上下文快照。"""

    @classmethod
    def build_snapshot(
        cls,
        req: ChatRequest,
        *,
        provider_id: str | None = None,
        provider_name: str,
        model: str | None,
        history: list[dict[str, Any]],
        memory_entries: list[dict[str, Any]] | None = None,
        memory_policy: dict[str, Any] | None = None,
        provider_capabilities: dict[str, Any] | None = None,
    ) -> ContextSnapshot:
        request = asdict(req)
        slices = cls._build_slices(
            req,
            history=history,
            memory_entries=memory_entries,
            memory_policy=memory_policy,
            provider_name=provider_name,
            model=model,
            provider_capabilities=provider_capabilities,
        )
        source_refs = [
            {
                "slice": slice_.name,
                "source": slice_.source,
                "present": slice_.content is not None,
            }
            for slice_ in slices
        ]
        context_size = len(
            json.dumps(
                {
                    "request": request,
                    "slices": [asdict(slice_) for slice_ in slices],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return ContextSnapshot(
            conversation_id=req.conversation_id,
            provider_name_snapshot=provider_name,
            model=model,
            request=request,
            slices=slices,
            source_refs=source_refs,
            context_size=context_size,
            provider_id=provider_id,
        )

    @staticmethod
    def _build_slices(
        req: ChatRequest,
        *,
        history: list[dict[str, Any]],
        memory_entries: list[dict[str, Any]] | None,
        memory_policy: dict[str, Any] | None,
        provider_name: str,
        model: str | None,
        provider_capabilities: dict[str, Any] | None,
    ) -> list[ContextSlice]:
        tool_names = [tool.name for tool in req.tools] if req.tools is not None else None
        provider_state = {
            "provider_name": provider_name,
            "provider_name_snapshot": provider_name,
            "model": model,
            "capabilities": provider_capabilities or {},
        }
        policy_state = memory_policy or {
            "version": "v1",
            "name": "default_context_policy",
            "history_messages": len(history),
            "trimmed": False,
        }
        return [
            ContextSlice(
                name="conversation_history",
                source="ConversationRepo.load_messages_as_anthropic",
                content=history,
            ),
            ContextSlice(
                name="runtime_state",
                source="AIAgent runtime context",
                content={
                    "conversation_id": req.conversation_id,
                    "provider_name": provider_name,
                    "provider_name_snapshot": provider_name,
                    "model": model,
                    "agent_id": req.agent_id,
                    "message_count": len(req.messages),
                },
            ),
            ContextSlice(
                name="memory_state",
                source="MemoryPolicy + MemoryRepo.list_relevant_entries",
                content={
                    "policy": memory_policy or {"version": "v1", "name": "default_memory_policy"},
                    "entries": memory_entries,
                },
            ),
            ContextSlice(name="tool_state", source="ChatRequest.tools", content=tool_names),
            ContextSlice(name="skill_state", source="not implemented yet", content=None),
            ContextSlice(name="provider_state", source="BaseProvider capabilities", content=provider_state),
            ContextSlice(name="policy_state", source="default context policy", content=policy_state),
        ]

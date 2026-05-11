"""AuditHookManager —— 五类自动 hook 写入 `audit_events`(B5 wave 2)。

封装策略(CLAUDE.md ⭐):
- 单类 `AuditHookManager` 编排所有 hook;不暴露模块级 free fn
- 持 sessionmaker(可为 None,退化 no-op);每次 record 开自己的 session,避免
  外层事务冲突
- 异常吞掉,best-effort —— 跟 TraceWriter 同款契约(audit 失败不阻断主链路)
- 事件类型 ClassVar 常量,跨层引用统一

五类事件(对照 design doc):
- `tool_call_pre`:ToolExecutionService.execute_tool_call 入口
- `tool_call_post`:同上,退出时
- `guardrail_verdict`:GuardrailEngine 命中 verdict≠ALLOW
- `memory_store`:MemoryRepo 写入(create/update/pin/archive 等改动)
- `checkpoint_create`:wave 3 接
- `rollback`:wave 3 接
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar

from chariot.repos.audit_repo import AuditRepo

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


_LOG = logging.getLogger("chariot.audit")


class AuditHookManager:
    """五类自动 hook 的写入入口。

    用法::

        hooks = AuditHookManager(sessionmaker)
        await hooks.record_tool_call_pre(tool_name="shell_exec", args={...}, turn_id="...")
        await hooks.record_guardrail_verdict(rule_id="shell_rm_rf", verdict="deny", ...)
        ...
    """

    # 事件类型 ClassVar(跨层引用一致;`audit_events.event_type` 列就这几个值)
    EVENT_TOOL_CALL_PRE: ClassVar[str] = "tool_call_pre"
    EVENT_TOOL_CALL_POST: ClassVar[str] = "tool_call_post"
    EVENT_GUARDRAIL_VERDICT: ClassVar[str] = "guardrail_verdict"
    EVENT_MEMORY_STORE: ClassVar[str] = "memory_store"
    EVENT_CHECKPOINT_CREATE: ClassVar[str] = "checkpoint_create"
    EVENT_ROLLBACK: ClassVar[str] = "rollback"
    EVENT_SKILL_STORE: ClassVar[str] = "skill_store"  # B6 wave 1:create/update/delete/enable/disable

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession] | None) -> None:
        self._sessionmaker = sessionmaker
        self._disabled = sessionmaker is None

    @property
    def enabled(self) -> bool:
        return not self._disabled

    async def record(
        self,
        event_type: str,
        *,
        status: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """通用 record;best-effort,任何异常吞掉。"""
        if self._disabled:
            return
        try:
            assert self._sessionmaker is not None
            async with self._sessionmaker() as session:
                await AuditRepo(session).create(
                    event_type=event_type,
                    status=status,
                    payload=payload or {},
                )
        except Exception as exc:  # pragma: no cover - best-effort 容错
            _LOG.warning("audit hook %s write failed: %s", event_type, exc)

    # ---- 五类事件专属 helper(让 caller 写一行而不是手拼 payload) ----

    async def record_tool_call_pre(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        tool_use_id: str | None = None,
        turn_id: str | None = None,
    ) -> None:
        await self.record(
            self.EVENT_TOOL_CALL_PRE,
            payload={
                "tool_name": tool_name,
                "args": args,
                "tool_use_id": tool_use_id,
                "turn_id": turn_id,
            },
        )

    async def record_tool_call_post(
        self,
        *,
        tool_name: str,
        is_error: bool,
        duration_ms: int | None = None,
        tool_use_id: str | None = None,
        turn_id: str | None = None,
    ) -> None:
        await self.record(
            self.EVENT_TOOL_CALL_POST,
            status="error" if is_error else "ok",
            payload={
                "tool_name": tool_name,
                "is_error": is_error,
                "duration_ms": duration_ms,
                "tool_use_id": tool_use_id,
                "turn_id": turn_id,
            },
        )

    async def record_guardrail_verdict(
        self,
        *,
        rule_id: str,
        verdict: str,
        tool_name: str,
        matched_pattern: str | None,
        quota_remaining: int | None,
        quota_exhausted: bool,
        tool_use_id: str | None = None,
    ) -> None:
        await self.record(
            self.EVENT_GUARDRAIL_VERDICT,
            status=verdict,
            payload={
                "rule_id": rule_id,
                "verdict": verdict,
                "tool_name": tool_name,
                "matched_pattern": matched_pattern,
                "quota_remaining": quota_remaining,
                "quota_exhausted": quota_exhausted,
                "tool_use_id": tool_use_id,
            },
        )

    async def record_memory_store(
        self,
        *,
        memory_id: str,
        action: str,  # 'create' / 'update' / 'pin' / 'archive' / 'delete'
        kind: str | None = None,
        pinned: bool | None = None,
    ) -> None:
        await self.record(
            self.EVENT_MEMORY_STORE,
            status=action,
            payload={
                "memory_id": memory_id,
                "action": action,
                "kind": kind,
                "pinned": pinned,
            },
        )

    async def record_skill_store(
        self,
        *,
        skill_id: str,
        name: str,
        action: str,  # 'create' / 'update' / 'enable' / 'disable' / 'delete'
        source: str = "manual",  # 'manual'(CLI/sidecar)/ 'propose'(wave 3 agent)
        proposer: str | None = None,  # wave 3 propose 时记 agent identifier
        checkpoint_id: str | None = None,  # wave 3 auto-checkpoint 后传入
    ) -> None:
        """B6:skill 写入路径自动审计。

        - wave 1:CLI / sidecar 手工 install/enable/disable/delete 时调
        - wave 3:`propose_skill` tool 走完整链路时调,source='propose'
        """
        await self.record(
            self.EVENT_SKILL_STORE,
            status=action,
            payload={
                "skill_id": skill_id,
                "name": name,
                "action": action,
                "source": source,
                "proposer": proposer,
                "checkpoint_id": checkpoint_id,
            },
        )

    async def record_checkpoint_create(
        self,
        *,
        checkpoint_id: str,
        name: str,
        kind: str,
        target: str | None = None,
    ) -> None:
        """wave 3 由 CheckpointManager.create 调。"""
        await self.record(
            self.EVENT_CHECKPOINT_CREATE,
            payload={
                "checkpoint_id": checkpoint_id,
                "name": name,
                "kind": kind,
                "target": target,
            },
        )

    async def record_rollback(
        self,
        *,
        checkpoint_id: str,
        ok: bool,
        restored: list[str] | None = None,
        errors: list[str] | None = None,
    ) -> None:
        """wave 3 由 CheckpointManager.rollback 调。"""
        await self.record(
            self.EVENT_ROLLBACK,
            status="ok" if ok else "error",
            payload={
                "checkpoint_id": checkpoint_id,
                "ok": ok,
                "restored": restored or [],
                "errors": errors or [],
            },
        )

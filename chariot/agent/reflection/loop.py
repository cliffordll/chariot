"""ReflectionLoop —— 反思-重试编排(B4 wave 2)。

职责:
1. 监听一次主 agent 跑完后的产出(assistant text + tool_result is_error)
2. 触发条件命中 → 调 critic
3. critic verdict=FAIL 且 retry 预算未尽 → 构造 [REFLECTION] 注入的下一轮 req
4. circuit breaker 防反思死循环

封装策略(CLAUDE.md ⭐):
- `AssistantBuffer` 收 ChatEvent 流,提取 assistant_text + tool_failed 标记
- `ReflectionLoop` 持 state(iteration / 累计记录),无外部副作用 — 主链路控制流由 AIAgent 主导
- `ReflectionRecord` frozen dataclass 记录每次 retry 的 verdict / 触发原因
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest, Message
from chariot.agent.reflection.critic import CriticVerdict

if TYPE_CHECKING:
    from chariot.agent.reflection.critic import CriticAgent


# 主 agent 自报失败的模式(case-insensitive):"FAILED:" / "失败:" / "ERROR:"
_SELF_REPORT_FAIL_PATTERN = re.compile(r"^\s*(FAILED|FAIL|失败|ERROR)\s*[:。]", re.MULTILINE | re.IGNORECASE)


@dataclass
class AssistantBuffer:
    """跟着 ChatEvent 流积累一次 run 的关键信号。"""

    text_parts: list[str] = field(default_factory=list)
    tool_failures: int = 0
    saw_error_event: bool = False
    error_type: str | None = None

    def accept(self, ev: ChatEvent) -> None:
        if ev.kind == "content_block_delta":
            delta = ev.delta or {}
            if delta.get("type") == "text_delta":
                text = delta.get("text", "")
                if isinstance(text, str):
                    self.text_parts.append(text)
        elif ev.kind == "tool_result" and ev.is_error:
            self.tool_failures += 1
        elif ev.kind == "error":
            self.saw_error_event = True
            self.error_type = ev.error_type

    @property
    def assistant_text(self) -> str:
        return "".join(self.text_parts).strip()

    def has_self_report_fail(self) -> bool:
        return bool(_SELF_REPORT_FAIL_PATTERN.search(self.assistant_text))


@dataclass(frozen=True)
class ReflectionRecord:
    """一次 reflection 迭代的记录(写进 trace_turns.meta.reflection 列表)。"""

    iteration: int
    trigger: str  # 'tool_failure' / 'self_report_fail' / 'error_event'
    verdict: str  # 'PASS' / 'FAIL' / 'UNSURE'
    reason: str
    retry_decision: str  # 'retry' / 'pass_stop' / 'budget_exhausted' / 'circuit_open' / 'verdict_unsure_stop'


@dataclass(frozen=True)
class ReflectionStep:
    """一次反思 step 的对外结果。"""

    should_retry: bool
    revised_req: ChatRequest | None
    record: ReflectionRecord


class ReflectionLoop:
    """单次"反思跑"的状态机。每次 AIAgent.run_chat 启动一个新实例。"""

    REFLECTION_INSTRUCTION = (
        "[REFLECTION]\ncritic verdict: {verdict}\ncritic reason: {reason}\n\n请基于上面的反馈重新尝试该任务。"
    )

    def __init__(
        self,
        *,
        critic: CriticAgent,
        max_retries: int = 2,
        circuit_breaker_consecutive_fail: int = 3,
    ) -> None:
        if max_retries < 0:
            raise ValueError(f"max_retries 必须 >= 0,got {max_retries}")
        if circuit_breaker_consecutive_fail < 1:
            raise ValueError(f"circuit_breaker_consecutive_fail 必须 >= 1,got {circuit_breaker_consecutive_fail}")
        self._critic = critic
        self._max_retries = max_retries
        self._circuit_consecutive_fail = circuit_breaker_consecutive_fail
        self._iteration = 0
        self._consecutive_fails = 0
        self._records: list[ReflectionRecord] = []

    @property
    def iteration(self) -> int:
        return self._iteration

    @property
    def records(self) -> list[ReflectionRecord]:
        return list(self._records)

    @staticmethod
    def detect_trigger(buffer: AssistantBuffer) -> str | None:
        """返触发原因 string;无触发返 None。优先级:error_event > tool_failure > self_report_fail。"""
        if buffer.saw_error_event:
            return "error_event"
        if buffer.tool_failures > 0:
            return "tool_failure"
        if buffer.has_self_report_fail():
            return "self_report_fail"
        return None

    async def step(
        self,
        *,
        buffer: AssistantBuffer,
        original_req: ChatRequest,
    ) -> ReflectionStep | None:
        """跑一次反思 step。

        返:
        - `None` — 未触发反思(无 trigger);主调用方直接结束
        - `ReflectionStep(should_retry=False, ...)` — 触发但不重试(PASS / 预算尽 / circuit 断)
        - `ReflectionStep(should_retry=True, revised_req=..., ...)` — 重试

        注:`self._iteration` 在 step() 调用前自增;`should_retry=True` 时调用方
        需用 `revised_req` 跑下一轮,跑完再调一次 step()。
        """
        trigger = self.detect_trigger(buffer)
        if trigger is None:
            return None  # 无反思触发,主流程结束

        self._iteration += 1

        # circuit breaker:连续 N 次 FAIL 直接跳出
        if self._consecutive_fails >= self._circuit_consecutive_fail:
            record = ReflectionRecord(
                iteration=self._iteration,
                trigger=trigger,
                verdict="(skipped)",
                reason=f"circuit breaker 触发(连续 {self._consecutive_fails} 次 FAIL)",
                retry_decision="circuit_open",
            )
            self._records.append(record)
            return ReflectionStep(should_retry=False, revised_req=None, record=record)

        # 跑 critic
        verdict = await self._critic.critique(
            task_goal=original_req.last_user_text() or "(未给出明确目标)",
            produced=buffer.assistant_text,
            extra_context=self._build_extra_context(buffer),
        )

        if verdict.verdict == "PASS":
            self._consecutive_fails = 0
            record = self._make_record(trigger, verdict, retry_decision="pass_stop")
            self._records.append(record)
            return ReflectionStep(should_retry=False, revised_req=None, record=record)

        if verdict.verdict == "UNSURE":
            # UNSURE 不算 fail;不消耗 retry 预算也不重试(critic 给不了明确信号)
            record = self._make_record(trigger, verdict, retry_decision="verdict_unsure_stop")
            self._records.append(record)
            return ReflectionStep(should_retry=False, revised_req=None, record=record)

        # FAIL
        self._consecutive_fails += 1
        if self._iteration > self._max_retries:
            record = self._make_record(trigger, verdict, retry_decision="budget_exhausted")
            self._records.append(record)
            return ReflectionStep(should_retry=False, revised_req=None, record=record)

        revised = self._build_revised_request(original_req, verdict)
        record = self._make_record(trigger, verdict, retry_decision="retry")
        self._records.append(record)
        return ReflectionStep(should_retry=True, revised_req=revised, record=record)

    # ---- 内部 ----

    @staticmethod
    def _build_extra_context(buffer: AssistantBuffer) -> str | None:
        notes: list[str] = []
        if buffer.tool_failures:
            notes.append(f"该轮有 {buffer.tool_failures} 个工具调用失败")
        if buffer.saw_error_event:
            notes.append(f"流式 error event 触发:{buffer.error_type}")
        return "\n".join(notes) if notes else None

    @classmethod
    def _build_revised_request(cls, prev: ChatRequest, verdict: CriticVerdict) -> ChatRequest:
        instruction = cls.REFLECTION_INSTRUCTION.format(verdict=verdict.verdict, reason=verdict.reason)
        new_messages = list(prev.messages)
        new_messages.append(Message(role="user", content=instruction))
        return dataclasses.replace(prev, messages=new_messages)

    def _make_record(self, trigger: str, verdict: CriticVerdict, *, retry_decision: str) -> ReflectionRecord:
        return ReflectionRecord(
            iteration=self._iteration,
            trigger=trigger,
            verdict=verdict.verdict,
            reason=verdict.reason,
            retry_decision=retry_decision,
        )

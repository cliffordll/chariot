"""CriticAgent —— 强契约的"裁判 LLM"(B4 wave 1)。

设计动机:reflect-then-retry 需要一个"评判主 agent 产出对不对"的副 LLM。直接
让主 agent 自评不可靠;独立 critic 用低温 + 强 VERDICT 契约可拿到稳定信号。

封装策略(CLAUDE.md ⭐):
- 复用 B3 wave 2 的 `AuxiliaryClient`,走 `build_request` / `generate` 原语
- `CriticAgent` 是特化壳:强 system prompt + VERDICT parser,封装内聚
- `CriticVerdict` frozen dataclass,verdict 字段 Literal 三态
- parser 兜底:critic 输出不符合契约 → `verdict=UNSURE`,raw 留证(给人复盘 prompt)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import Message

if TYPE_CHECKING:
    from chariot.agent.auxiliary_client import AuxiliaryClient
    from chariot.models.auxiliary import AuxiliaryClientEntry
    from chariot.providers.base import BaseProvider


CRITIC_AUX_NAME = "critic"
"""`auxiliary_clients` 表里 critic 行的固定 name(bootstrap 时按这个找)。"""


Verdict = Literal["PASS", "FAIL", "UNSURE"]


@dataclass(frozen=True)
class CriticVerdict:
    """critic 一次裁决结果。

    - `verdict`:三态字符串(parser 兜底,LLM 不听话也能给个 UNSURE)
    - `reason`:critic 给的解释 / 改进建议(parser 失败时记 "输出不符合契约:<raw>")
    - `raw`:原始 LLM 输出全文,给 trace_turns.meta.reflection 留证
    """

    verdict: Verdict
    reason: str
    raw: str


_VERDICT_LINE_PATTERN = re.compile(r"^\s*VERDICT\s*:\s*(PASS|FAIL|UNSURE)\s*$", re.MULTILINE)


class CriticAgent:
    """裁判 LLM:跑一次 critique,返结构化 verdict。

    用法::

        critic = CriticAgent.from_auxiliary_clients(aux_entries, providers)
        if critic is not None:
            verdict = await critic.critique(
                task_goal="写一个排序函数",
                produced="def sort(x): return x",
            )
            # verdict.verdict == "FAIL"
    """

    SYSTEM_PROMPT = (
        "你是任务评审 critic。读完下面的「任务目标」+「产出」+(可选)「上下文」,"
        "回答**严格两段**:\n\n"
        "第一行必须形如:\nVERDICT: PASS|FAIL|UNSURE\n"
        "其它内容(reason / 改进建议)放第二段。\n\n"
        "VERDICT 含义:\n"
        "- PASS:产出满足任务要求,可交付\n"
        "- FAIL:有明确缺陷,需重做(reason 必须给出具体偏差)\n"
        "- UNSURE:信息不足以裁决(reason 说明缺什么信息)\n\n"
        "只输出这两段,不要 preamble,不要解释 VERDICT 含义本身。"
    )

    def __init__(self, aux: AuxiliaryClient) -> None:
        self._aux = aux

    @property
    def aux(self) -> AuxiliaryClient:
        return self._aux

    @classmethod
    def from_auxiliary_clients(
        cls,
        aux_entries: list[AuxiliaryClientEntry],
        providers: dict[str, BaseProvider],
    ) -> CriticAgent | None:
        """扫 `aux_entries` 找 `name='critic'` 行,装好 CriticAgent 返回;
        找不到 / dangling provider_id 返 None(AIAgent.bootstrap 跳过装载)。"""
        from chariot.agent.auxiliary_client import AuxiliaryClient

        for entry in aux_entries:
            if entry.name != CRITIC_AUX_NAME:
                continue
            provider = providers.get(entry.provider_id)
            if provider is None:
                return None  # dangling reference
            return cls(AuxiliaryClient(entry=entry, provider=provider))
        return None

    async def critique(
        self,
        *,
        task_goal: str,
        produced: str,
        extra_context: str | None = None,
    ) -> CriticVerdict:
        """跑一次 critique;parse VERDICT。失败 → verdict=UNSURE。"""
        prompt = self._build_user_prompt(task_goal=task_goal, produced=produced, extra_context=extra_context)
        req = self._aux.build_request(
            messages=[Message(role="user", content=prompt)],
            system=self.SYSTEM_PROMPT,
        )
        chunks: list[str] = []
        async for ev in self._aux.generate(req):
            if ev.kind == "error":
                return CriticVerdict(
                    verdict="UNSURE",
                    reason=f"critic 上游错误: {ev.error_type}: {ev.error_message}",
                    raw="",
                )
            text = self._extract_text(ev)
            if text:
                chunks.append(text)
        raw = "".join(chunks).strip()
        return self._parse_verdict(raw)

    # ---- 内部 ----

    @staticmethod
    def _build_user_prompt(*, task_goal: str, produced: str, extra_context: str | None) -> str:
        parts = [
            "[任务目标]",
            task_goal.strip(),
            "",
            "[产出]",
            produced.strip(),
        ]
        if extra_context and extra_context.strip():
            parts.extend(["", "[上下文]", extra_context.strip()])
        return "\n".join(parts)

    @staticmethod
    def _extract_text(ev: ChatEvent) -> str:
        if ev.kind != "content_block_delta":
            return ""
        delta = ev.delta or {}
        if delta.get("type") != "text_delta":
            return ""
        text = delta.get("text", "")
        return text if isinstance(text, str) else ""

    @staticmethod
    def _parse_verdict(raw: str) -> CriticVerdict:
        if not raw:
            return CriticVerdict(verdict="UNSURE", reason="critic 输出为空", raw=raw)
        match = _VERDICT_LINE_PATTERN.search(raw)
        if match is None:
            preview = raw[:200].replace("\n", " ⏎ ")
            return CriticVerdict(
                verdict="UNSURE",
                reason=f"critic 输出不符合 VERDICT 契约: {preview}",
                raw=raw,
            )
        verdict: Verdict = match.group(1)  # type: ignore[assignment]
        # reason = VERDICT 行之后的剩余内容(去掉前导空行)
        reason = raw[match.end() :].strip() or "(no reason provided)"
        return CriticVerdict(verdict=verdict, reason=reason, raw=raw)

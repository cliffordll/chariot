"""EvalRunner —— 编排 golden task 跑批。

数据流:
1. `agent_factory(task)` 拿 AIAgent
2. 构造 stateful ChatRequest(每个 task 一个 conversation_id;trace 借这个 id 反查)
3. `agent.run_chat(req)` 消费事件流 → 累积 final_response + 抓 error event
4. 用 conversation_id 从 trace_turns 里反查 turn_id
5. `TraceCostExtractor` 填 token / cost / duration / tool_calls 到 RunRecord
6. `VERIFIERS[task.verifier_type]()` 判定 → record.verdict / reason

跟 AIAgent 解耦:不直接 import AIAgent,通过 AgentFactory 拿一个能 run_chat /
拿 session_maker 的对象。这样 CLI 注入真 agent;pytest CI 注入 stub(不调
网络)做结构性测试;未来 B6 注入"带 skill 预激活的 agent"做 ablation。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from ulid import ULID

from chariot.agent.chat_request import ChatRequest, Message
from chariot.eval.cost import TraceCostExtractor
from chariot.eval.verifiers import VERIFIERS
from chariot.models.eval import GoldenTask, RunRecord, Verdict, VerifierResult
from chariot.repos.trace_repo import TraceRepo

if TYPE_CHECKING:
    from chariot.agent.run import AIAgent


AgentFactory = Callable[[GoldenTask], "AIAgent"]
"""每个 task 一个 AIAgent 实例。CLI 注入真 agent;pytest 注入 stub。"""


class EvalRunner:
    """跑 golden task 集合 → RunRecord 集合。

    Constructor 接 AgentFactory + verifier registry(可注入 mock 做 unit 测试)。
    """

    def __init__(
        self,
        *,
        agent_factory: AgentFactory,
        verifiers: dict[str, type] | None = None,
        agent_profile: str | None = None,
    ) -> None:
        self._agent_factory = agent_factory
        self._verifiers = verifiers if verifiers is not None else VERIFIERS
        self._agent_profile = agent_profile  # 非空时塞进 ChatRequest.agent_profile

    async def run_task(self, task: GoldenTask) -> RunRecord:
        """跑一个 task → RunRecord。

        异常路径:
        - agent_factory 抛 → ERROR
        - verifier_type 不在 registry → ERROR
        - run_chat 流抛 → ERROR(stream 内 error event 转 ERROR;其它异常包成 ERROR)
        - verifier.verify 抛 → ERROR(verifier 该自己 except 返 VerifierResult,但兜底)
        """
        record = RunRecord(task_id=task.task_id, verdict=Verdict.ERROR)
        t0 = time.perf_counter()
        try:
            agent = self._agent_factory(task)
        except Exception as exc:
            record.reason = f"agent_factory failed: {exc}"
            record.error = repr(exc)
            return record

        verifier_cls = self._verifiers.get(task.verifier_type)
        if verifier_cls is None:
            record.reason = f"unknown verifier_type {task.verifier_type!r}"
            return record

        conversation_id = str(ULID())
        req = ChatRequest(
            provider_name="",  # AIAgent 内部按 active default 路由(stub 也忽略)
            messages=[Message(role="user", content=task.prompt)],
            conversation_id=conversation_id,
            agent_profile=self._agent_profile,
            model=task.model,
            system=task.system,
        )
        # 真 agent 默认 provider 由 bootstrap 时确定;factory 注入时若不设 provider_name,
        # 走 stub 路径 / CLI 把默认填好。这里不强行设防,留给 factory 决定。
        if not req.provider_name:
            req = self._fill_default_provider(req, agent)

        final_response_buf: list[str] = []
        last_error_type: str | None = None
        last_error_message: str | None = None
        try:
            async for event in agent.run_chat(req):
                if event.kind == "content_block_delta" and event.delta:
                    if event.delta.get("type") == "text_delta":
                        text = event.delta.get("text")
                        if isinstance(text, str):
                            final_response_buf.append(text)
                elif event.kind == "error":
                    last_error_type = event.error_type
                    last_error_message = event.error_message
        except Exception as exc:
            record.duration_seconds = time.perf_counter() - t0
            record.reason = f"run_chat raised: {exc}"
            record.error = repr(exc)
            return record

        record.final_response = "".join(final_response_buf)
        record.duration_seconds = time.perf_counter() - t0

        if last_error_type is not None:
            record.error = f"{last_error_type}: {last_error_message or ''}"
            record.reason = f"agent yielded error event: {last_error_type}"
            record.verdict = Verdict.ERROR
            await self._fill_trace(record, agent, conversation_id)
            return record

        await self._fill_trace(record, agent, conversation_id)

        try:
            result: VerifierResult = verifier_cls().verify(task, record)
        except Exception as exc:
            record.reason = f"verifier raised: {exc}"
            record.error = repr(exc)
            return record
        record.verdict = result.verdict
        record.reason = result.reason
        return record

    @staticmethod
    def _fill_default_provider(req: ChatRequest, agent: AIAgent) -> ChatRequest:
        """req.provider_name 空 → 抓 agent 第一个 provider 当默认(stub 测试场景)。"""
        import dataclasses

        providers = getattr(agent, "_providers", {})
        if not providers:
            return req
        return dataclasses.replace(req, provider_name=next(iter(providers)))

    @staticmethod
    async def _fill_trace(record: RunRecord, agent: AIAgent, conversation_id: str) -> None:
        """从 trace_turns + trace_tool_calls 反查填 cost / tools。失败静默(eval 仍能跑)。"""
        try:
            async with agent.session_maker() as session:
                repo = TraceRepo(session)
                turns = await repo.list_turns(conversation_id=conversation_id, limit=1)
                if not turns:
                    return
                await TraceCostExtractor(repo).populate(record, turns[0].id)
        except RuntimeError:
            # agent 未装载 sessionmaker → no-op,record 保持默认值
            return

    async def run_all(self, tasks: list[GoldenTask]) -> list[RunRecord]:
        """串行跑所有 task。

        串行是有意的:避免 provider rate limit + tool side effect 互相干扰。
        未来要并行加 `--parallel` flag,不动默认行为。
        """
        records: list[RunRecord] = []
        for task in tasks:
            records.append(await self.run_task(task))
        return records

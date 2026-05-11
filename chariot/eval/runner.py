"""EvalRunner —— 编排 golden task 跑批。

Wave 1 范围(本文件):
- AgentFactory 注入点(`Callable[[GoldenTask], AIAgent]`)
- 数据流形:`run_task(task)` 接 GoldenTask,返 RunRecord
- 真执行 / verifier dispatch / trace 反查 — 留到 wave 3 填

为什么 wave 1 就建 Runner?方便 wave 2 写 seed task 时,test 能 import 出
Runner shape 跑结构性测试(stub agent + stub verifier);wave 3 再补真实现。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from chariot.models.eval import GoldenTask, RunRecord, Verdict

if TYPE_CHECKING:
    from chariot.agent.run import AIAgent


AgentFactory = Callable[[GoldenTask], "AIAgent"]
"""每个 task 一个 AIAgent 实例。CLI 注入真 agent;pytest 注入 stub。"""


class EvalRunner:
    """跑 golden task 集合 → RunRecord 集合。

    跟 AIAgent 解耦:不直接 import AIAgent,通过 AgentFactory 拿到一个能 run_chat
    的对象。这样 CLI 注入连真 model 的 AIAgent;pytest CI 注入 stub agent(不调
    网络)做结构性测试;未来 B6 注入"带 skill 预激活的 agent"做 ablation。
    """

    def __init__(self, *, agent_factory: AgentFactory) -> None:
        self._agent_factory = agent_factory

    async def run_task(self, task: GoldenTask) -> RunRecord:
        """跑一个 task → RunRecord。

        Wave 1 占位实现:直接返 ERROR(reason='runner not implemented')。
        Wave 3 会填:agent_factory(task) → run_chat(prompt) → 等 stream_done →
        从 trace_turns / trace_tool_calls 反查填 RunRecord → dispatch verifier。
        """
        del self  # wave 1 不用 factory
        return RunRecord(
            task_id=task.task_id,
            verdict=Verdict.ERROR,
            reason="EvalRunner.run_task not implemented (B2 wave 3)",
        )

    async def run_all(self, tasks: list[GoldenTask]) -> list[RunRecord]:
        """串行跑所有 task。

        串行是有意的:避免 provider rate limit + tool side effect 互相干扰。
        万一未来要并行,加 `--parallel` flag,不动默认行为。
        """
        records: list[RunRecord] = []
        for task in tasks:
            records.append(await self.run_task(task))
        return records

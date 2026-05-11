"""sidecar critic RPC adapter(B4 wave 1)。

仅暴露:
- `critique_text`:跑一次 critique,返 verdict + reason + raw
- `get_critic_config`:返当前装载的 critic auxiliary entry(给桌面端做"是否
  装了 critic"判断 + 展示用)
"""

from __future__ import annotations

from typing import Any

from chariot.rpc.jsonrpc import JsonRpcServer, RpcContext, RpcError
from chariot.sidecar.methods import MethodBase


class CriticMethods(MethodBase):
    async def critique(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """`critique_text`:跑一次 critique。

        params:`{task_goal: str, produced: str, extra_context?: str}`
        返:`{verdict: PASS|FAIL|UNSURE, reason: str, raw: str}`
        critic 未装载 → ERR_NOT_FOUND。
        """
        del ctx
        task_goal = self._require_str(params, "task_goal")
        produced = self._require_str(params, "produced")
        extra_context = self._optional_str(params, "extra_context")
        critic = self.agent.critic_agent
        if critic is None:
            raise RpcError(
                JsonRpcServer.ERR_NOT_FOUND,
                "no critic loaded — add an auxiliary_clients row with name='critic'",
            )
        verdict = await critic.critique(
            task_goal=task_goal,
            produced=produced,
            extra_context=extra_context,
        )
        return {
            "verdict": verdict.verdict,
            "reason": verdict.reason,
            "raw": verdict.raw,
        }

    async def config(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """`get_critic_config`:返当前装载的 critic entry 概览;未装载返
        `{loaded: false}`。"""
        del params, ctx
        critic = self.agent.critic_agent
        if critic is None:
            return {"loaded": False}
        entry = critic.aux.entry
        return {
            "loaded": True,
            "auxiliary_client": {
                "name": entry.name,
                "provider_entry": entry.provider_entry,
                "model": entry.model,
                "params": entry.params,
            },
        }

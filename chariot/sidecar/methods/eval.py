"""sidecar eval RPC adapters(B2 wave 5)。"""

from __future__ import annotations

from typing import Any

from chariot.rpc.jsonrpc import RpcContext
from chariot.sidecar.methods import MethodBase
from chariot.sidecar.runtime import SidecarRuntime
from chariot.sidecar.services.eval import EvalApi


class EvalMethods(MethodBase):
    def __init__(self, runtime: SidecarRuntime) -> None:
        super().__init__(runtime)
        self._service = EvalApi(runtime)

    async def list_golden_tasks(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        golden_dir = self._optional_str(params, "golden_dir")
        return await self._service.list_golden_tasks(golden_dir=golden_dir)

    async def list_eval_runs(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        runs_dir = self._optional_str(params, "runs_dir")
        return await self._service.list_eval_runs(runs_dir=runs_dir)

    async def get_eval_run(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        run_id = self._require_str(params, "run_id")
        runs_dir = self._optional_str(params, "runs_dir")
        return await self._service.get_eval_run(run_id=run_id, runs_dir=runs_dir)

    async def diff_eval_runs(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        del ctx
        baseline_id = self._require_str(params, "baseline_id")
        current_id = self._require_str(params, "current_id")
        runs_dir = self._optional_str(params, "runs_dir")
        return await self._service.diff_runs(
            baseline_id=baseline_id,
            current_id=current_id,
            runs_dir=runs_dir,
        )

"""sidecar RL RPC adapters(B7 wave 1)。

读路径单一:`export_trajectory({conversation_id, out_path?, raw?})` → 落 JSONL
返 `{out_path, row_count, scrub_mode}`。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chariot.rl import NullScrubber, SecretScrubber, TrajectoryExporter
from chariot.rpc.jsonrpc import RpcContext
from chariot.sidecar.methods import MethodBase


class RLMethods(MethodBase):
    async def export_trajectory(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """B7 wave 1:`export_trajectory({conversation_id, out_path?, raw?: bool})`.

        - `conversation_id`:必填,源 conversation id
        - `out_path`:可选,落盘绝对路径;不传走默认
          `~/.chariot/rl/trajectories/<conv-id>.jsonl`
        - `raw`:可选 bool,默认 False;True → 跳 SecretScrubber(慎用)
        """
        del ctx
        conversation_id = self._require_str(params, "conversation_id")
        raw = bool(params.get("raw", False))
        out_str = self._optional_str(params, "out_path")
        out_path = Path(out_str) if out_str else self._default_out_path(conversation_id)
        scrubber = NullScrubber() if raw else SecretScrubber()
        exporter = TrajectoryExporter(
            runtime=self.agent,
            scrubber=scrubber,
            audit_hooks=self.agent.audit_hooks,
        )
        row_count = await exporter.export_to_jsonl(conversation_id, out_path)
        return {
            "out_path": str(out_path),
            "row_count": row_count,
            "scrub_mode": "raw" if raw else "default",
        }

    @staticmethod
    def _default_out_path(conversation_id: str) -> Path:
        return Path.home() / ".chariot" / "rl" / "trajectories" / f"{conversation_id}.jsonl"

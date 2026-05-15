"""sidecar log method handlers(0.6.5 S.8c)。

`LogMethods`:list_logs(按时间窗口列 log)。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from chariot.database.models import LogEntry
from chariot.rpc.jsonrpc import JsonRpcServer, RpcContext, RpcError
from chariot.sidecar.methods import MethodBase
from chariot.sidecar.services import LogApi


class LogMethods(MethodBase):
    """log method handlers(list_logs)。"""

    _DEFAULT_LIMIT = 100
    _MAX_LIMIT = 1000

    async def list_(self, params: dict[str, Any], ctx: RpcContext) -> dict[str, Any]:
        """`list_logs`:按时间窗口列 log。

        params(全可选):
        - `limit`:返条数,默认 100,上限 1000(防客户端拉爆 UI / 内存)
        - `offset`:跳过条数,默认 0
        - `since`:ISO 8601 datetime 字符串,严格大于(polling 游标用)
        - `until`:ISO 8601 datetime 字符串,小于等于
        """
        del ctx
        limit = self._parse_limit(params.get("limit"))
        offset = self._parse_offset(params.get("offset"))
        since = self._parse_datetime(params, key="since")
        until = self._parse_datetime(params, key="until")
        entries = await LogApi(self.runtime).list_logs(limit=limit, offset=offset, since=since, until=until)
        return {"logs": [self._serialize(e) for e in entries]}

    @classmethod
    def _parse_limit(cls, raw: object) -> int:
        if raw is None:
            return cls._DEFAULT_LIMIT
        if not isinstance(raw, int) or isinstance(raw, bool) or raw <= 0:
            raise RpcError(
                JsonRpcServer.ERR_INVALID_PARAMS,
                "'limit' must be a positive integer",
            )
        return min(raw, cls._MAX_LIMIT)

    @staticmethod
    def _parse_offset(raw: object) -> int:
        if raw is None:
            return 0
        if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
            raise RpcError(
                JsonRpcServer.ERR_INVALID_PARAMS,
                "'offset' must be a non-negative integer",
            )
        return raw

    @staticmethod
    def _parse_datetime(params: dict[str, Any], *, key: str) -> datetime | None:
        raw = params.get(key)
        if raw is None:
            return None
        if not isinstance(raw, str):
            raise RpcError(
                JsonRpcServer.ERR_INVALID_PARAMS,
                f"{key!r} must be an ISO 8601 datetime string",
            )
        try:
            return datetime.fromisoformat(raw)
        except ValueError as e:
            raise RpcError(
                JsonRpcServer.ERR_INVALID_PARAMS,
                f"{key!r} is not a valid ISO 8601 datetime: {e}",
            ) from e

    @staticmethod
    def _serialize(entry: LogEntry) -> dict[str, Any]:
        """`LogEntry` ORM row → wire dict(datetime → ISO str)。"""
        return {
            "id": entry.id,
            "provider": entry.provider,
            "input_tokens": entry.input_tokens,
            "output_tokens": entry.output_tokens,
            "latency_ms": entry.latency_ms,
            "status": entry.status,
            "error": entry.error,
            "created_at": entry.created_at.isoformat(),
        }

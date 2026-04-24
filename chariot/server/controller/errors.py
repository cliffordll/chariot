"""客户端错误响应体工厂。

controller 收到 service 的 `ServiceError` 或业务失败时,用 `chariot_error(code, message, **extra)`
构造响应 body;结构统一成
`{"error": {"type": "chariot_error", "code": ..., "message": ..., **extra}}`。
客户端按 `error.code` 判错因,按 HTTP status 判 client/server error 方向。

`code` 命名约定:snake_case,描述错因类别(而非具体消息)。
"""

from __future__ import annotations

from typing import Any


def chariot_error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": "chariot_error",
        "code": code,
        "message": message,
    }
    body.update(extra)
    return {"error": body}

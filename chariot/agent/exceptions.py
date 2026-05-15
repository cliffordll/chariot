"""AIAgent 内核异常。

库化后 chariot 不暴露 HTTP,异常由 surface 层(CLI / sidecar / gateways)
各自映射成自己的呈现形态:CLI 红字 / sidecar JSON-RPC error / Gateway 平台消息。

四个异常类型:

- `ProviderError`:Provider 层(LLM 上游)出错。code / message / status 三元组,
  status 给后续 surface 想转 HTTP 时参考(默认 502 = upstream)
- `ConfigError`:配置层错(startup 装 ChariotConfig 时 / DB schema 错时)
- `ToolExecutionError`:工具执行错;**作为 ChatEvent 的输入**(被 AgentLoop
  捕获后转 `tool_result(is_error=True)` 而非直接抛给 surface)
- `ConversationLockTimeout`:双层锁等待超时(进程内 30s / DB 锁重试 3 次失败);
  surface 层映射为 `ChatEvent(kind="error", error_type="conversation_busy_*")`

模块级零自由函数。所有异常都是 Exception 子类,不放 dataclass 风格(异常用
class 风更直接;dataclass 异常 frozen 跟 raise / __init__ 串签名冲突)。
"""

from __future__ import annotations

from typing import Any


class ProviderError(Exception):
    """Provider 层错误(LLM 上游或 Provider 自身配置)。

    status / code / message 三元组(无 `extra` 字段,具体附加字段由调用方在
    子类里加,避免泛 dict)。

    使用场景:
    - 200 前的 HTTP 错(401 / 5xx / 网络断)→ Provider.generate raise
      ProviderError → AIAgent 捕获后转 ChatEvent(kind="error")
    - Provider 配置不合法(api_key 缺失等)→ 在 create 时 raise

    code 枚举:
    - upstream_auth_failed(401 / 403)
    - upstream_unreachable(connect / DNS / TLS 错)
    - upstream_server_error(5xx)
    - upstream_stream_error(200 已发后中途 IO 错;但这种**不抛**,
      yield ChatEvent kind="error" 即可。这里留作子类语义对齐)
    - rate_limited(429)
    - invalid_options(create 配置错)
    """

    def __init__(self, code: str, message: str, *, status: int = 502) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


class ConfigError(Exception):
    """配置层错误;startup 装 ChariotConfig / DB schema 错时 raise。

    子类用于让 surface 精确映射呈现形态,不靠 message 文本子串判断。
    """


class ProviderNotFound(ConfigError):  # noqa: N818 — 短名对调用方更友好,语义明显是异常
    """指定 provider ref(id / slug / 兼容 legacy name)在 `providers` 表里找不到。"""


class DuplicateProviderName(ConfigError):  # noqa: N818 — 同上
    """provider 稳定引用冲突(create / copy / reslug 目标 slug 冲突)。"""


class ToolNotFound(ConfigError):  # noqa: N818 — 同上
    """指定 tool name 在 `tools` 表里找不到。"""


class ConversationNotFound(ConfigError):  # noqa: N818 — 同上
    """指定 conversation id 在 `conversations` 表里找不到。"""


class DuplicateConversationId(ConfigError):  # noqa: N818 — 同上
    """conversation id 已存在(显式 create 路径撞已用 id)。"""


class AuxiliaryClientNotFound(ConfigError):  # noqa: N818 — 短名对调用方更友好
    """指定 auxiliary_client name 在 `auxiliary_clients` 表里找不到。"""


class DuplicateAuxiliaryClientName(ConfigError):  # noqa: N818 — 同上
    """auxiliary_client name 已存在(create 时撞已用 name)。"""


class ToolExecutionError(Exception):
    """Tool.execute 抛的内部异常。

    AgentLoop 在 `_execute_tools` 里捕获后转
    `ChatEvent(kind="tool_result", is_error=True, content=str(exc))`,
    **不直接传给 surface**。Tool 实现里 raise 这个表达"工具内部失败"。
    """

    def __init__(self, message: str, *, tool_name: str | None = None, **extra: Any) -> None:
        self.message = message
        self.tool_name = tool_name
        self.extra = extra
        super().__init__(message)


class ConversationLockTimeout(Exception):  # noqa: N818 — 'Timeout' 后缀语义同 'Error',不重复加
    """双层锁等待超时。

    两个触发场景:
    - 进程内 asyncio.Lock 等 > 30s
    - DB advisory lock 等 > busy_timeout * 3 次重试

    AIAgent 捕获后转 `ChatEvent(kind="error", error_type="conversation_busy_*")`
    给 surface(local / db 用 layer 字段区分)。
    """

    def __init__(self, message: str, *, layer: str) -> None:
        """layer:'local'(进程内 asyncio.Lock)/ 'db'(SQLite advisory)。"""
        self.message = message
        self.layer = layer
        super().__init__(message)

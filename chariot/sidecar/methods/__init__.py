"""sidecar 业务 method handlers(0.6.5 S.8b/c+)。

文件布局
========
- `__init__.py`(本文件):`SidecarAgent` Protocol + `MethodBase` 共享基 +
  `register_methods()` 集中注册入口
- `chat.py`:`ChatMethod` + `_RequestDecoder`(chat method handler)
- `convo.py`:`ConvoMethods`(convo CRUD)
- `tool.py`:`ToolMethods`(tool CRUD,只能改 enabled / options)
- `provider.py`:`ProviderMethods`(provider CRUD + probe)
- `log.py`:`LogMethods`(log 查询)

method 列表(15 条):
- chat:跑一次 chat,流式 notify ChatEvent + 终止 response
- list_convos / get_convo / rename_convo / delete_convo
- list_tools / enable_tool / disable_tool / config_tool
- list_providers / add_provider / edit_provider / delete_provider / probe_provider
- list_logs

约束(对照 hermes 教训,详 docs/DESIGN.md §11.4)
- 业务逻辑住此包,**不**回调 RPC 框架内部;框架接口只用 `RpcContext.notify`
  / 抛 `RpcError(code, message)`
- handler 抛 `RpcError` → 透传 code 给客户端;抛非 RpcError → 框架转
  `ERR_INTERNAL`,不 leak 内部细节
- 已知限制(0.6.5 S.8c 范围):provider / tool CRUD **不自动 reload AIAgent**
  — cached BaseProvider / BaseTool 实例不变,下次 chat 仍用旧实例。要让新
  CRUD 立即生效需重启 sidecar。0.6.6+ 加 `reload_agent` method 解决
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest
from chariot.agent.exceptions import (
    ConfigError,
    ConvoNotFound,
    DuplicateConvoId,
    DuplicateProviderName,
    ProviderNotFound,
    ToolNotFound,
)
from chariot.rpc.jsonrpc import JsonRpcServer, RpcError

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


# 唯一对外公开 API:`register_methods`。`MethodBase` / `SidecarAgent` 是包内
# 共享契约,sub-modules `from chariot.sidecar.methods import MethodBase` 是合法的
# (同包内引用),但不进 `__all__`(`from chariot.sidecar.methods import *` 只
# 看到一个名字)。
__all__ = ["register_methods"]


class SidecarAgent(Protocol):
    """`AIAgent` 兼容的最小接口(只用 run + session_maker)。

    用 Protocol 而不是直接 `AIAgent` 是为了:
    - 测试:mock agent 不继承 AIAgent,duck-typed 实现这两个 attr 即可
    - 生产:`AIAgent` 结构上满足这个 Protocol
    """

    def run(self, req: ChatRequest) -> AsyncIterator[ChatEvent]: ...

    @property
    def session_maker(self) -> async_sessionmaker[AsyncSession]: ...


class MethodBase:
    """sidecar method handler 类的共享基(给 chat / convo / tool / provider / log 继承)。

    职责:
    1. 持 `agent` 引用(子类 handler 通过 `self.agent` 访问 run / session_maker)
    2. `_session()` 异步 ctx mgr — 开 session + 翻译 repo 异常 → RpcError
    3. `_require_*` / `_optional_*` — 参数校验工具

    位置选择:这俩是 5 个 noun 文件都用的横切工具,装在 `methods/__init__.py`
    让 noun 子类 `from chariot.sidecar.methods import MethodBase` 一行 import 即用。
    """

    def __init__(self, agent: SidecarAgent) -> None:
        self.agent = agent

    @asynccontextmanager
    async def _session(self) -> AsyncGenerator[AsyncSession, None]:
        """开 DB session + 翻译 repo 抛的语义异常 → RpcError。

        异常映射:
        - ConvoNotFound / ProviderNotFound / ToolNotFound → ERR_NOT_FOUND
        - DuplicateConvoId / DuplicateProviderName → ERR_DUPLICATE
        - ConfigError(及子类:InvalidProviderOptions 等)→ ERR_INVALID_PARAMS
        - 其它非 RpcError 异常 → 不抓,让框架转 ERR_INTERNAL
        """
        async with self.agent.session_maker() as session:
            try:
                yield session
            except (ConvoNotFound, ProviderNotFound, ToolNotFound) as e:
                raise RpcError(JsonRpcServer.ERR_NOT_FOUND, str(e)) from e
            except (DuplicateConvoId, DuplicateProviderName) as e:
                raise RpcError(JsonRpcServer.ERR_DUPLICATE, str(e)) from e
            except ConfigError as e:
                raise RpcError(JsonRpcServer.ERR_INVALID_PARAMS, str(e)) from e

    @staticmethod
    def _require_str(params: dict[str, Any], key: str) -> str:
        """params[key] 必须是非空字符串,否则抛 RpcError。"""
        val = params.get(key)
        if not isinstance(val, str) or not val:
            raise RpcError(
                JsonRpcServer.ERR_INVALID_PARAMS,
                f"{key!r} is required and must be a non-empty string",
            )
        return val

    @staticmethod
    def _require_dict(params: dict[str, Any], key: str) -> dict[str, Any]:
        """params[key] 必须是 object,否则抛 RpcError。"""
        val = params.get(key)
        if not isinstance(val, dict):
            raise RpcError(
                JsonRpcServer.ERR_INVALID_PARAMS,
                f"{key!r} is required and must be an object",
            )
        return cast(dict[str, Any], val)

    @staticmethod
    def _optional_dict(params: dict[str, Any], key: str) -> dict[str, Any] | None:
        """params[key] 是 object 或 null/缺失。Null/缺 → None;非 object 抛 RpcError。"""
        val = params.get(key)
        if val is None:
            return None
        if not isinstance(val, dict):
            raise RpcError(
                JsonRpcServer.ERR_INVALID_PARAMS,
                f"{key!r} must be an object or null",
            )
        return cast(dict[str, Any], val)

    @staticmethod
    def _optional_str(params: dict[str, Any], key: str) -> str | None:
        """params[key] 是 string 或 null/缺失。Null/缺 → None;非 string 抛 RpcError。"""
        val = params.get(key)
        if val is None:
            return None
        if not isinstance(val, str):
            raise RpcError(
                JsonRpcServer.ERR_INVALID_PARAMS,
                f"{key!r} must be a string or null",
            )
        return val


def register_methods(
    server: JsonRpcServer,
    agent: SidecarAgent,
    *,
    db_path: Path,
) -> None:
    """把 sidecar 所有 15 个业务 method 注册到给定 server(显式调用)。

    `agent` 由调用方装载好后传入(典型 `__main__` 通过
    `AgentRegistry.reserve("sidecar", db_path=...)`);测试场景可传 mock。
    `db_path` 给 ChatMethod 在 per-call override 路径上调用 `AgentRegistry.reserve`
    用(0.6.6+,Chat 页对齐 CLI 的 `--base-url` / `--api-key`);默认 agent 路径
    不读这个值,所以测试随便给个 path 都行。

    重复注册 method 名抛 `ValueError`(JsonRpcServer 兜底)。

    注意 import 是惰性的(函数体内做)— noun 子文件都
    `from chariot.sidecar.methods import MethodBase`,顶层 import 各 noun 模块会
    触发循环;惰性 import 让 `__init__.py` 加载完后再装载子模块。
    """
    # 子模块惰性 import:`__init__` 加载完 → 调 register_methods → 这时各
    # noun 文件首次 import,它们 `from chariot.sidecar.methods import MethodBase`
    # 已经成立
    from chariot.sidecar.methods.chat import ChatMethod
    from chariot.sidecar.methods.convo import ConvoMethods
    from chariot.sidecar.methods.log import LogMethods
    from chariot.sidecar.methods.provider import ProviderMethods
    from chariot.sidecar.methods.tool import ToolMethods

    server.method("chat")(ChatMethod(agent, db_path=db_path))

    convos = ConvoMethods(agent)
    server.method("list_convos")(convos.list_)
    server.method("get_convo")(convos.get)
    server.method("rename_convo")(convos.rename)
    server.method("delete_convo")(convos.delete)

    tools = ToolMethods(agent)
    server.method("list_tools")(tools.list_)
    server.method("enable_tool")(tools.enable)
    server.method("disable_tool")(tools.disable)
    server.method("config_tool")(tools.config)

    providers = ProviderMethods(agent)
    server.method("list_providers")(providers.list_)
    server.method("add_provider")(providers.add)
    server.method("edit_provider")(providers.edit)
    server.method("delete_provider")(providers.delete)
    server.method("probe_provider")(providers.probe)

    logs = LogMethods(agent)
    server.method("list_logs")(logs.list_)

"""Newline-delimited stdio JSON-RPC server(0.6.5 S.8a)。

协议
----
- newline-delimited JSON,每帧一行(以 `\\n` 结尾)
- request:`{"jsonrpc":"2.0", "id":..., "method":..., "params":...}`
- response:`{"jsonrpc":"2.0", "id":..., "result":...}` 或
            `{"jsonrpc":"2.0", "id":..., "error":{"code":..., "message":...}}`
- notify:`{"jsonrpc":"2.0", "method":..., "params":...}`(无 id;server 主推
  给客户端,不期望客户端回 response;典型用例:chat method 推流式 ChatEvent)

不实现
------
- **batch**(JSON-RPC 数组形式)—— sidecar / ACP / MCP 实测不需要,YAGNI
- **id 严格 spec**—— 不区分 "id 字段缺失"(spec 里的 notification)vs
  "id: null"(spec 里的 expects-null-id-response)。两者都 → 不发 response。
  实测 sidecar 客户端从不发 id: null,这条简化无影响

并发
----
- 每个 request 单独起 `asyncio.create_task` dispatch,允许多 method 并发跑
  (典型场景:chat 跑着,客户端再调 list_logs)
- write 路径用 `asyncio.Lock` 保护 —— 多个 handler 并发推 notify 帧时,字节
  不交错(JSON 行级原子)

EOF
---
- reader 读到 `b''` → 主循环 break → cancel 在飞 task → gather → return
- **不主动 kill 进程**(对齐 CLAUDE.md sidecar 生命周期约定:stdin 关闭 = 自然
  退出 graceful)

模块级零自由函数(CLAUDE.md ⭐),所有逻辑收进 `RpcError` / `RpcContext` /
`JsonRpcServer` 三个类。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar, Protocol, cast

logger = logging.getLogger(__name__)


class _Writer(Protocol):
    """`asyncio.StreamWriter` 兼容的最小接口(只用 write + drain)。

    用 Protocol 而不是直接 `asyncio.StreamWriter` 是为了:
    - 测试:MockWriter 不继承 StreamWriter,但符合此 Protocol,pyright 静态
      检查通过
    - 生产:`asyncio.streams.StreamWriter` 结构上满足这个 Protocol(write +
      drain 签名一致),无需 cast
    """

    def write(self, data: bytes) -> None: ...
    async def drain(self) -> None: ...


class RpcError(Exception):
    """业务 method handler 显式抛 → server 转 error response 带原 code。

    handler 应该用 `RpcError(code, message)` 表达"业务能识别的错"(配置不合法 /
    参数缺失 / 资源不存在等),server 透传给客户端。

    抛**非** RpcError 的异常(`RuntimeError` / `ValueError` 等)→ server 转
    `ERR_INTERNAL`,**不 leak** 异常 message / traceback 给客户端(避免内部
    实现细节暴露;详细信息走 `logger.exception` 进 stderr / 日志文件)。
    """

    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class RpcContext:
    """Method handler 上下文:暴露 notify 推送通道。

    server 在 dispatch 每个 request 时给 handler 注入 ctx;handler 通过
    `await ctx.notify(method, params)` 推 notify 帧。典型用例:chat method
    每个 ChatEvent 一个 `notify("chat_event", asdict(event))`。

    一个 server 实例共享一个 ctx(无 per-request 状态);server 单 writer,
    并发 dispatch 时 ctx.notify 通过 server 的 write_lock 串行化。
    """

    def __init__(self, server: JsonRpcServer) -> None:
        self._server = server

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        """推一帧 notify 给客户端。客户端收到后按 method 名 dispatch payload。"""
        await self._server.notify(method, params)


# 类型别名:method handler 签名。**注意**这是 runtime 赋值(不是 annotation),
# 内部的 RpcContext 需要在赋值前定义好 —— 所以放在 RpcContext 之后,
# JsonRpcServer 之前。
MethodHandler = Callable[[dict[str, Any], RpcContext], Awaitable[dict[str, Any] | None]]


class JsonRpcServer:
    """Newline-delimited stdio JSON-RPC server。无业务知识,纯协议层。

    使用方式::

        server = JsonRpcServer()

        @server.method("chat")
        async def chat(params, ctx):
            await ctx.notify("chat_event", {"kind": "message_start"})
            return {"stream_id": "..."}

        @server.method("list_convos")
        async def list_convos(params, ctx):
            return {"convos": [...]}

        # 接 stdio:
        reader, writer = await _stdio_pair()  # 见 chariot/sidecar/__main__.py
        await server.serve(reader, writer)
    """

    # JSON-RPC 2.0 标准错误码(spec 第 5.1 节)
    ERR_PARSE: ClassVar[int] = -32700
    ERR_INVALID_REQUEST: ClassVar[int] = -32600
    ERR_METHOD_NOT_FOUND: ClassVar[int] = -32601
    ERR_INVALID_PARAMS: ClassVar[int] = -32602
    ERR_INTERNAL: ClassVar[int] = -32603

    # 应用层错误码(spec 允许 -32099 ~ -32000 实现自定义范围)
    ERR_NOT_FOUND: ClassVar[int] = -32001
    ERR_DUPLICATE: ClassVar[int] = -32002

    def __init__(self) -> None:
        self._methods: dict[str, MethodHandler] = {}
        self._writer: _Writer | None = None
        self._write_lock = asyncio.Lock()

    # ---- 注册 ----

    def method(self, name: str) -> Callable[[MethodHandler], MethodHandler]:
        """装饰器:注册一个 method handler。

        重复注册同 name → `ValueError`(避免 import 顺序导致的覆盖错误)。
        """

        def decorator(handler: MethodHandler) -> MethodHandler:
            if name in self._methods:
                raise ValueError(f"duplicate method: {name!r}")
            self._methods[name] = handler
            return handler

        return decorator

    def known_methods(self) -> set[str]:
        """已注册的 method 名集合(给 introspection / `methods.list` 之类用)。"""
        return set(self._methods.keys())

    # ---- 主循环 ----

    async def serve(self, reader: asyncio.StreamReader, writer: _Writer) -> None:
        """读帧 → dispatch → 写 response。EOF 优雅 return。

        每帧起一个 dispatch task,互不阻塞。**EOF 后不 cancel** 在飞 task —
        客户端关 stdin 时通常已经在等仍未送达的 response(典型:发完最后一个
        request 就 close stdin),cancel 反而丢 response。改为等所有 handler
        自然跑完后 return。

        如果未来需要"强制关闭超时未完成"语义,加 `shutdown_grace_period_s`
        参数 + asyncio.wait_for 包 gather。0.6.5 阶段 sidecar 单 client、
        请求短,不需要这个。
        """
        self._writer = writer
        ctx = RpcContext(self)
        pending: list[asyncio.Task[None]] = []
        try:
            while True:
                try:
                    line = await reader.readline()
                except (ConnectionResetError, asyncio.IncompleteReadError):
                    break  # 客户端断开
                if not line:
                    break  # EOF
                task = asyncio.create_task(self._dispatch(line, ctx))
                pending.append(task)
                # 顺手 GC 已完成 task,避免 list 无限涨
                pending = [t for t in pending if not t.done()]
            # EOF 收尾:等所有在飞 handler 跑完(它们的 response 写出去)
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        finally:
            self._writer = None

    # ---- dispatch ----

    async def _dispatch(self, line: bytes, ctx: RpcContext) -> None:
        """处理单帧:解析 → 校验 → 路由 → 跑 handler → 写 response。"""
        # 1. 解析 JSON
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as e:
            await self._write_error(None, self.ERR_PARSE, f"parse error: {e}")
            return

        # 2. 校验 envelope
        if not isinstance(parsed, dict):
            await self._write_error(None, self.ERR_INVALID_REQUEST, "request must be a JSON object")
            return
        # cast 给 pyright:JSON 解出来理论是 dict[Any, Any];本函数后续按 key 取值
        # 都做 isinstance 校验,所以 Any 不会 leak 出来
        req = cast(dict[str, Any], parsed)

        rid = req.get("id")
        method_name = req.get("method")
        params_raw = req.get("params")

        if not isinstance(method_name, str) or not method_name:
            await self._write_error(
                rid, self.ERR_INVALID_REQUEST, "method must be a non-empty string"
            )
            return

        if params_raw is None:
            params: dict[str, Any] = {}
        elif isinstance(params_raw, dict):
            params = cast(dict[str, Any], params_raw)
        else:
            await self._write_error(rid, self.ERR_INVALID_PARAMS, "params must be a JSON object")
            return

        # 3. 路由 method
        handler = self._methods.get(method_name)
        if handler is None:
            await self._write_error(
                rid, self.ERR_METHOD_NOT_FOUND, f"method not found: {method_name!r}"
            )
            return

        # 4. 跑 handler
        try:
            result = await handler(params, ctx)
        except RpcError as e:
            await self._write_error(rid, e.code, e.message)
            return
        except asyncio.CancelledError:
            # EOF 时 server 主动 cancel,不该被吞 —— re-raise 让 task 正常结束
            raise
        except Exception as e:
            # 非 RpcError 的内部异常:不 leak 细节给客户端,详细 traceback 进 log
            logger.exception("handler %r raised", method_name)
            await self._write_error(rid, self.ERR_INTERNAL, f"internal error: {type(e).__name__}")
            return

        # 5. 写 response —— 仅当客户端期望(rid 非 None)
        if rid is None:
            return  # notification:无 response
        await self._write_response(rid, result if result is not None else {})

    # ---- 写帧 ----

    async def _write_response(self, rid: Any, result: dict[str, Any]) -> None:
        await self._write_frame({"jsonrpc": "2.0", "id": rid, "result": result})

    async def _write_error(self, rid: Any, code: int, message: str) -> None:
        await self._write_frame(
            {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": code, "message": message},
            }
        )

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        """主动推一帧 notify 给客户端(无 id;不期望客户端回 response)。

        典型用例:chat method 推 `notify("chat_event", asdict(event))`。serve
        之外或 shutdown 中调 → 静默丢弃(详见 _write_frame 注释)。
        """
        await self._write_frame({"jsonrpc": "2.0", "method": method, "params": params})

    async def _write_frame(self, obj: dict[str, Any]) -> None:
        """写一帧。Lock 保护避免并发 notify 帧字节交错。

        客户端断开时 writer.drain 会抛 ConnectionResetError / BrokenPipeError;
        这里静默吃掉(主循环很快会读到 EOF 退出,没必要重复抛)。
        """
        writer = self._writer
        if writer is None:
            return  # serve 之外或 shutdown 中调 → 静默丢弃
        line = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._write_lock:
            try:
                writer.write(line)
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                pass  # 客户端断了

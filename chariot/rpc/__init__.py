"""Cross-surface RPC framework — sidecar / acp / mcp 共享(0.6.5 S.8a 起)。

只装协议层(JSON-RPC 帧 / dispatcher / 错误码),**不**装业务方法实现 ——
业务 method handler 住各 surface 自己的目录(`sidecar/methods.py` /
`acp/handlers.py` / `mcp/handlers.py` 等),通过 `@server.method("name")`
装饰器注册到 `JsonRpcServer` 实例。

边界(对照 hermes-agent 的反面教材):
- `rpc/jsonrpc.py` **不引用** `sidecar/` / `acp/` / `mcp/`(框架反向依赖
  业务层 = 反模式,会让"加新 surface"难度抬高)
- `rpc/` 只装跟 RPC 协议本身相关的东西 —— 不当 `common/` / `utils/` 用,
  避免目录腐化(详见 DESIGN.md §11.4)
"""

from chariot.rpc.jsonrpc import JsonRpcServer, RpcContext, RpcError

__all__ = ["JsonRpcServer", "RpcContext", "RpcError"]

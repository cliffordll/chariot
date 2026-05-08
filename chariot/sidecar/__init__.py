"""Tauri 桌面壳的 Python sidecar(0.6.5 S.8b 起)。

由 Tauri 主进程 spawn,通过 stdin/stdout 双向 stdio JSON-RPC 跟前端通信。
跟 chariot CLI 平级,**单一 surface**(单进程 / 单 client / 单 session),
不复用 CLI 的 typer 入口。

目录组织
========
- `__main__.py`:进程入口(`python -m chariot.sidecar`),装载 + 起 server + cleanup
- `methods/`:业务 method handlers + register_methods 集中注册
  - `__init__.py`:`SidecarAgent` Protocol + `MethodBase` 共享基 + `register_methods()`
  - `chat.py` / `convo.py` / `tool.py` / `provider.py` / `log.py`:每文件一个 noun

帧 / dispatch / 错误码框架不在这 —— 在 `chariot.rpc.jsonrpc`(给后续
ACP / MCP 共享)。
"""

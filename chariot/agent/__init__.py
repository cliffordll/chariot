"""Chariot AIAgent 内核(0.6.0+)。

狭义内核 —— 只装"运行 AIAgent 必需的运行时":协议无关 IO 类型
(`ChatRequest` / `ChatEvent`)、AIAgent 主类(`run.py`)、agent 主循环
(`loop.py`)、配置 / 异常 / 进程内锁。

不属于这里的:
- 数据持久化(`chariot/repos/`)
- 工具实现(`chariot/tools/`)
- LLM 后端(`chariot/providers/`)
- 进程通信(`chariot/rpc/`)
- Surface(`chariot/cli/` / `chariot/sidecar/` / `chariot/gateways/` 等)

详见 `docs/DESIGN.md` §6。
"""

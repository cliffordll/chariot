"""Service 层:跨 controller / agent 的共用业务工具。

当前模块:
- `exceptions.ServiceError`:统一的 HTTP 错误形态(controller 异常处理器映射成响应)
- `log_writer.LogWriter` / `log_writer`:logs 表落库器(Agent 写日志唯一入口)
- `model_prober.ModelProber`:模型探针(临时 build entry + 发最小请求,验证可达)

这一层不处理 HTTP 协议(controller 的职责),也不直接写 SQL(repository 的职责);
只做"拿到请求参数 → 调 repository / model → 决策 / 包装结果"。
"""

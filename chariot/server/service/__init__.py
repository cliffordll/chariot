"""Service 层:数据面业务逻辑。

- `forwarder.Forwarder` / `forwarder.forwarder`:httpx 转发 + 跨格式翻译编排
- `selector.pick_upstream`:按 `x-chariot-upstream` header 选 upstream

这一层不处理 HTTP 协议(controller 的职责),也不直接写 SQL(repository 的职责);
只做"拿到请求参数 → 调 repository 取数据 → 决策 / 转发 / 翻译"。
"""

"""ModelProber —— 对配置 entry 跑一次最小请求,判断该 model 是否真能工作。

工作机制
--------
1. `ModelRegistry.build(entry)` 临时构造 Model 实例
   - 失败(api_key 缺 / type 未注册等)→ `ProbeResult(ok=False, code='config_error')`
2. 发送最小 messages 请求(`max_tokens=1`,内容 `ping`),非流式
   - 成功 → `ProbeResult(ok=True, latency_ms)`
   - `ServiceError` → `ProbeResult(ok=False, error 透传 code/message)`
3. 临时 Model 实例不显式 close;函数返回后由 GC 回收
   (probe 不频繁,容忍每次新建一个 httpx 连接池)

费用警告
--------
真打上游 1 次,**消耗 ~1 token**(取决于上游计费规则);UI 上要明确告知用户。
MockModel 走纯本地路径,probe 不发 HTTP、零费用。

封装
----
`ModelProber` 是无状态工具类,只暴露 `probe(entry)` 一个 classmethod。所有解析 /
错误转换都收在类内,模块级零自由函数 / 零可变变量(`_PROBE_BODY` 是 ClassVar
常量)。
"""

from __future__ import annotations

import json
import time
from typing import ClassVar

from pydantic import BaseModel

from chariot.agent.config import ConfigError, ModelEntry
from chariot.server.model.registry import ModelRegistry
from chariot.server.service.exceptions import ServiceError


class ProbeError(BaseModel):
    """探针失败时的错误结构。

    `code` 透传 ServiceError.code(`upstream_auth_failed` / `upstream_unreachable`
    / `upstream_timeout` / `upstream_server_error` / `invalid_json_body` ...),
    或 chariot 内置错因(`config_error` / `probe_internal_error`)。
    """

    code: str
    message: str


class ProbeResult(BaseModel):
    """探针结果。`ok=True` 时 error=None;`ok=False` 时 error 必给。"""

    ok: bool
    latency_ms: int
    error: ProbeError | None = None


class ModelProber:
    """探针工具类 —— 给一条 ModelEntry,临时 build + 发最小请求 + 报结果。"""

    # 最小合法 Messages 请求体;model 字段会被 AnthropicModel 按 entry.options.model
    # 改写,这里写啥都行。
    _PROBE_BODY: ClassVar[bytes] = json.dumps(
        {
            "model": "probe",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        }
    ).encode("utf-8")

    @classmethod
    async def probe(cls, entry: ModelEntry) -> ProbeResult:
        """对 entry 执行一次探针。永不 raise —— 任何失败都包成 ProbeResult。"""
        t0 = time.monotonic()
        try:
            model = ModelRegistry.build(entry)
        except ConfigError as e:
            # build 阶段失败:latency 设 0(还没发任何请求)
            return ProbeResult(
                ok=False,
                latency_ms=0,
                error=ProbeError(code="config_error", message=str(e)),
            )

        try:
            await model.respond(cls._PROBE_BODY, stream=False)
        except ServiceError as e:
            return ProbeResult(
                ok=False,
                latency_ms=cls._elapsed_ms(t0),
                error=ProbeError(code=e.code, message=e.message),
            )
        except Exception as e:  # pragma: no cover —— 兜底,避免 probe 把 server 拉爆
            return ProbeResult(
                ok=False,
                latency_ms=cls._elapsed_ms(t0),
                error=ProbeError(code="probe_internal_error", message=str(e)),
            )

        return ProbeResult(ok=True, latency_ms=cls._elapsed_ms(t0))

    @staticmethod
    def _elapsed_ms(t0: float) -> int:
        return int((time.monotonic() - t0) * 1000)

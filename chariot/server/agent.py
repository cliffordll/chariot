"""Agent — chariot 智能体主类。

职责(0.3.1 路由模型重构后)
----------------------------
1. 持有 `name → Model` 实例字典(从 DB entries 构造)
2. 按 client 在 body.model 字段写的 entry name 路由,转给对应 `model.respond()`
3. 每次请求在 `logs` 表记一条(status / latency / model name)

0.3.1 之前 Agent 有 active 状态、单 model 实例;0.3.1 起 active 概念删除,
client 通过 body.model 显式选 entry。详见 `docs/DESIGN.md` §5。

后续版本可以在这层加(不影响 Controller / Model):
- 多轮对话状态 / 工具调用 / 自我进化循环 / Model ensemble

单例管理
--------
当前运行中的 agent 是 **类级** 单例(`_current` 唯一类级状态):

```python
Agent.install_from_config(config)   # app lifespan startup / 配置变更后调,全量重置
Agent.current()                     # 任意位置取当前 agent
Agent.uninstall()                   # lifespan shutdown / 测试 teardown
```

`install_from_config` 是幂等的全量重置 —— 既是首次安装,也是配置变更后的重建。
不再分 `install` / `refresh` 两个语义。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, ClassVar, cast

from fastapi.responses import Response

from chariot.server.config import ChariotConfig
from chariot.server.model.base import Model
from chariot.server.model.registry import ModelRegistry
from chariot.server.service.exceptions import ServiceError
from chariot.server.service.log_writer import log_writer

_log = logging.getLogger("chariot.server.agent")


class Agent:
    """chariot 智能体。0.3.1 起按 body.model 路由到对应 entry 的 Model 实例。

    对外:
    - `Agent.install_from_config(config)`:全量重置 `name → Model` 字典并注册成当前 agent
    - `Agent.current()`:取当前 agent
    - `Agent.handle(body)`:请求入口
    """

    # ---- 类级单例(唯一状态) ----

    _current: ClassVar[Agent | None] = None

    # ---- 实例构造 ----

    def __init__(self) -> None:
        self.models: dict[str, Model] = {}

    # ---- 单例管理(classmethod) ----

    @classmethod
    def install_from_config(cls, config: ChariotConfig) -> Agent:
        """按 `ChariotConfig` 全量重置 Agent:rebuild `name → Model` 字典 + 设为 current。

        - 每条 entry eager build —— 起步成本与 entry 数线性相关,实测够快
        - 既是首次安装也是配置变更后的重建(幂等):lifespan startup 调一次,
          admin entries CRUD 后再调即可
        - lifespan 期 `ConfigError` 直接上冒(server 拒启动);CRUD 路径由
          controller 层捕住转 502
        """
        agent = cls()
        for entry in config.models:
            agent.models[entry.name] = ModelRegistry.build(entry)
        cls._current = agent
        _log.info("agent installed with %d entries: %s", len(agent.models), list(agent.models))
        return agent

    @classmethod
    def current(cls) -> Agent:
        """取当前注册的 agent;未 install 直接 raise。"""
        if cls._current is None:
            raise RuntimeError(
                "Agent 未安装;在 app lifespan startup 里调 Agent.install_from_config()"
            )
        return cls._current

    @classmethod
    def uninstall(cls) -> None:
        """清除当前 agent 注册(lifespan shutdown / 测试 teardown)。"""
        cls._current = None

    # ---- 请求处理 ----

    async def handle(self, body: bytes) -> Response:
        """处理一次聊天请求。

        - 解析 body.model → 查 entry → 调 `model.respond(body)` 透传
        - body.model 缺失 / 空 / 未知 → 400(ServiceError `unknown_model_name`)
        - 记一条 log(model=客户端写的 name,status=ok/error)
        """
        t0 = time.monotonic()
        body_dict = self._parse_body_dict(body)
        model_name = self._extract_model_name(body_dict)
        is_stream = self._detect_stream(body_dict)

        if not model_name:
            err = ServiceError(
                status=400,
                code="unknown_model_name",
                message="body.model 字段必填:写 chariot 的 entry name(`chariot model list` 看可用)",
            )
            await self._record_log(None, "error", t0, error=f"{err.code}: {err.message}")
            raise err

        model = self.models.get(model_name)
        if model is None:
            known = ", ".join(self.models) or "(空)"
            err = ServiceError(
                status=400,
                code="unknown_model_name",
                message=f"未知 model name: {model_name!r};可选:{known}",
            )
            await self._record_log(model_name, "error", t0, error=f"{err.code}: {err.message}")
            raise err

        try:
            resp = await model.respond(body, stream=is_stream)
            await self._record_log(model_name, "ok", t0)
            return resp
        except ServiceError as e:
            await self._record_log(model_name, "error", t0, error=f"{e.code}: {e.message}")
            raise
        except Exception as e:  # pragma: no cover
            await self._record_log(model_name, "error", t0, error=str(e))
            raise

    # ---- 内部:body 解析 + 字段抽取 + 日志 ----

    @staticmethod
    def _parse_body_dict(body: bytes) -> dict[str, Any]:
        """body → dict;非法 JSON / 顶层非 dict → 返空 dict。

        `handle()` 不在这层做严格校验 —— 失败的 body 自然走到 'unknown_model_name'
        分支(空 dict 没 model 字段),保留单一错误码出口。
        """
        try:
            data: Any = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return cast(dict[str, Any], data) if isinstance(data, dict) else {}

    @staticmethod
    def _extract_model_name(body_dict: dict[str, Any]) -> str | None:
        """从已解析的 body 抽 `model` 字段;缺失 / 非 str / 空串 → None。"""
        m = body_dict.get("model")
        return m if isinstance(m, str) and m else None

    @staticmethod
    def _detect_stream(body_dict: dict[str, Any]) -> bool:
        """从已解析的 body 抽 `stream` 字段;非 True 一律视为 False。"""
        return body_dict.get("stream") is True

    async def _record_log(
        self,
        model: str | None,
        status: str,
        t0: float,
        *,
        error: str | None = None,
    ) -> None:
        """写一条请求流水;LogWriter 内部已兜底。"""
        latency_ms = int((time.monotonic() - t0) * 1000)
        await log_writer.record(
            model=model,
            status=status,  # type: ignore[arg-type]
            latency_ms=latency_ms,
            error=error,
        )

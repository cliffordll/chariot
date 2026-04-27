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
当前运行中的 agent 是 **类级** 单例:

```python
Agent.install_from_config(config)   # app lifespan startup 调一次
Agent.refresh_config(config)        # admin entries CRUD 后刷新缓存
Agent.current()                     # 任意位置取当前 agent
Agent.uninstall()                   # lifespan shutdown / 测试 teardown
```
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, ClassVar

from fastapi.responses import Response

from chariot.server.config import ChariotConfig, ConfigError, ModelEntry
from chariot.server.model.base import Model
from chariot.server.model.registry import ModelRegistry
from chariot.server.service.exceptions import ServiceError
from chariot.server.service.log_writer import log_writer

_log = logging.getLogger("chariot.server.agent")


class Agent:
    """chariot 智能体。0.3.1 起按 body.model 路由到对应 entry 的 Model 实例。

    对外:
    - `Agent.install_from_config(config)`:按配置构建 `name → Model` 字典并注册
    - `Agent.refresh_config(config)`:重建字典(entries CRUD 后调用)
    - `Agent.current()`:取当前 agent
    - `Agent.handle(body)`:请求入口
    """

    # ---- 类级单例 ----

    _current: ClassVar[Agent | None] = None
    _config: ClassVar[ChariotConfig | None] = None

    # ---- 实例构造 ----

    def __init__(self) -> None:
        self.models: dict[str, Model] = {}

    # ---- 单例管理(classmethod) ----

    @classmethod
    def install_from_config(cls, config: ChariotConfig) -> Agent:
        """按 `ChariotConfig` 构建 `name → Model` 字典并注册成当前 agent。

        - 每条 entry 立刻 build(eager)—— 起步成本与 entry 数线性相关,实测够快
        - lifespan startup 期 raise 的 `ConfigError` 直接上冒(让 server 拒启动)
        """
        agent = cls()
        for entry in config.models:
            agent.models[entry.name] = ModelRegistry.build(entry)
        cls._current = agent
        cls._config = config
        _log.info("agent installed with %d entries: %s", len(agent.models), list(agent.models))
        return agent

    @classmethod
    def refresh_config(cls, config: ChariotConfig) -> Agent:
        """根据新 config 重建 Model 字典(entries 增/删/改后调用)。

        实现策略:
        - 删 entries:从字典移除
        - 改 entries:整 rebuild 该 entry 的 Model 实例(可能因为 options 变了)
        - 加 entries:新 build
        - 简化:直接全量 rebuild,反正 build 不贵且 entry 数少
        """
        agent = cls.current()
        new_models: dict[str, Model] = {}
        for entry in config.models:
            new_models[entry.name] = ModelRegistry.build(entry)
        agent.models = new_models
        cls._config = config
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
    def config(cls) -> ChariotConfig:
        """取当前 agent 关联的 config;没装载过返 `ChariotConfig.empty()`。"""
        return cls._config if cls._config is not None else ChariotConfig.empty()

    @classmethod
    def uninstall(cls) -> None:
        """清除当前 agent 注册 + 配置状态(lifespan shutdown / 测试 teardown)。"""
        cls._current = None
        cls._config = None

    # ---- 请求处理 ----

    async def handle(self, body: bytes) -> Response:
        """处理一次聊天请求。

        - 解析 body.model → 查 entry → 调 `model.respond(body)` 透传
        - body.model 缺失 / 空 / 未知 → 400(ServiceError `unknown_model_name`)
        - 记一条 log(model=客户端写的 name,status=ok/error)
        """
        t0 = time.monotonic()
        model_name = self._extract_model_name(body)
        is_stream = self._detect_stream(body)

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

    # ---- 内部:body 探测 + 日志 ----

    @staticmethod
    def _detect_stream(body: bytes) -> bool:
        """粗扫 body 判断 stream=true;非法 JSON 视为 False。"""
        try:
            data: Any = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return False
        try:
            return data.get("stream") is True
        except AttributeError:
            return False

    @staticmethod
    def _extract_model_name(body: bytes) -> str | None:
        """从 body 抽 `model` 字段;缺失 / 非 str / 空串都返 None。"""
        try:
            data: Any = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        try:
            m = data.get("model")
        except AttributeError:
            return None
        if not isinstance(m, str) or not m:
            return None
        return m

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

    # ---- 内部:测试钩子 ----

    @classmethod
    def install_test_models(cls, models: dict[str, Model]) -> Agent:
        """测试专用:直接注入 name → Model 字典,不走 ModelRegistry.build。"""
        agent = cls()
        agent.models = dict(models)
        cls._current = agent
        # 给个最小 ChariotConfig 让 cls.config() 不返空
        cls._config = ChariotConfig(
            models=tuple(
                ModelEntry(name=name, type="mock", options={}, params={}) for name in models
            ),
        )
        return agent


# satisfy linters; ConfigError exported here for backward compat with old imports
__all__ = ["Agent", "ConfigError"]

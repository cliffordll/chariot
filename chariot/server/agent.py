"""Agent — chariot 智能体主类。

职责(0.2.0 极简)
------------------
1. 持有一个 `Model` 实现(没显式注入就用 `MockModel` 兜底)
2. 把客户端请求转给 `model.respond()`(只接 Anthropic Messages 协议)
3. 每次请求在 `logs` 表记一条(status / latency / model name)

后续版本可以在这层加(不影响 Controller / Model):
- 多轮对话状态
- 工具调用 / function calling
- 自我进化循环(chariot 的核心方向)
- Model ensemble / 动态切换

单例管理
--------
当前运行中的 agent 是 **类级** 单例:

```python
Agent.install(model=...)   # app lifespan startup 调一次
Agent.current()            # 任意位置取当前 agent
Agent.uninstall()          # lifespan shutdown / 测试 teardown
```

同一时间只存一个;`install` 第二次会覆盖第一次。模块级不暴露可变变量。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, ClassVar

from fastapi.responses import Response

from chariot.server.config import ChariotConfig, ConfigError
from chariot.server.model.base import Model
from chariot.server.model.mock import mock_model
from chariot.server.model.registry import ModelRegistry
from chariot.server.service.exceptions import ServiceError
from chariot.server.service.log_writer import log_writer

_log = logging.getLogger("chariot.server.agent")


class Agent:
    """chariot 智能体。0.2.0 薄壳 —— 请求直接转 `model`,外加日志埋点。

    对外三条路:
    - `Agent(model=...)`:任意构造一个实例(测试 / 独立使用)
    - `Agent.install(model=...)`:构造 + 注册为"当前运行的 agent"
    - `Agent.current()`:取当前 agent

    `handle()` 是请求入口;其它方法都是实现细节。
    """

    # ---- 类级单例 ----

    _current: ClassVar[Agent | None] = None
    _config: ClassVar[ChariotConfig | None] = None
    _active_name: ClassVar[str | None] = None

    # ---- 实例构造 ----

    def __init__(self, model: Model | None = None) -> None:
        """构造 agent。`model=None` 走 `MockModel` 兜底。"""
        self.model: Model = model if model is not None else mock_model

    # ---- 单例管理(classmethod) ----

    @classmethod
    def install(cls, *, model: Model | None = None) -> Agent:
        """构造一个 agent 并注册成当前运行实例。

        - app lifespan startup 里调一次(默认 `model=None` 走 MockModel)
        - 再调会覆盖上一个(动态切 model 时用得上)
        - 只动 `_current`;`_config` / `_active_name` 由 `install_from_config` /
          `switch_to` 各自维护
        """
        cls._current = cls(model)
        _log.info("agent installed with model=%s", cls._current.model.name)
        return cls._current

    @classmethod
    def install_from_config(cls, config: ChariotConfig) -> Agent:
        """按 `ChariotConfig` 注入 model 并安装为当前 agent。

        - `config.active_entry()` 为 None(无配置 / 无 active)→ MockModel fallback
        - 否则 `ModelRegistry.build(entry)` 构造对应实现
        - lifespan startup 期 raise 的 `ConfigError` 直接上冒(让 server 不要起来)
        - 同时记下 `_config` / `_active_name`,供 /admin/models 端点查询
        """
        entry = config.active_entry()
        if entry is None:
            agent = cls.install()
            cls._active_name = None
        else:
            agent = cls.install(model=ModelRegistry.build(entry))
            cls._active_name = entry.name
        cls._config = config
        return agent

    @classmethod
    def switch_to(cls, name: str) -> Agent:
        """运行时切到名为 `name` 的 model;不改 config 文件,仅换内存里的 active。

        在 `_config.models` 里按 name 找 entry,经 ModelRegistry 重建,覆盖 install。
        找不到 / build 失败 → `ConfigError`(由 controller 转 4xx/5xx)。
        """
        config = cls.config()
        for entry in config.models:
            if entry.name == name:
                model = ModelRegistry.build(entry)
                agent = cls.install(model=model)
                cls._active_name = name
                return agent
        known = ", ".join(e.name for e in config.models) or "(空)"
        raise ConfigError(f"未知 model name: {name!r};可选:{known}")

    @classmethod
    def current(cls) -> Agent:
        """取当前注册的 agent;未 install 直接 raise。"""
        if cls._current is None:
            raise RuntimeError("Agent 未安装;在 app lifespan startup 里调 Agent.install()")
        return cls._current

    @classmethod
    def config(cls) -> ChariotConfig:
        """取当前 agent 关联的 config;没装载过返 `ChariotConfig.empty()`。"""
        return cls._config if cls._config is not None else ChariotConfig.empty()

    @classmethod
    def active_name(cls) -> str | None:
        """当前 active 的 model name(来自 config 或 switch_to);MockModel fallback 返 None。"""
        return cls._active_name

    @classmethod
    def uninstall(cls) -> None:
        """清除当前 agent 注册 + 配置状态(lifespan shutdown / 测试 teardown)。"""
        cls._current = None
        cls._config = None
        cls._active_name = None

    # ---- 请求处理 ----

    async def handle(self, body: bytes) -> Response:
        """处理一次聊天请求。

        - 按 `body.stream` 决定返回 StreamingResponse 还是 Response
        - 记一条 log(status=ok/error + latency_ms + model=client hint)
        - `ServiceError` 直接 re-raise(由 controller `exception_handler` 转 HTTP)
        """
        t0 = time.monotonic()
        model_hint = self._detect_model_hint(body)
        is_stream = self._detect_stream(body)

        try:
            resp = await self.model.respond(body, stream=is_stream)
            await self._record_log(model_hint, "ok", t0)
            return resp
        except ServiceError as e:
            await self._record_log(model_hint, "error", t0, error=f"{e.code}: {e.message}")
            raise
        except Exception as e:  # pragma: no cover
            await self._record_log(model_hint, "error", t0, error=str(e))
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
    def _detect_model_hint(body: bytes) -> str | None:
        """从 body 抽 `model` 字段作日志展示用;拿不到就 None。"""
        try:
            data: Any = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        try:
            m = data.get("model")
        except AttributeError:
            return None
        return m if isinstance(m, str) else None

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

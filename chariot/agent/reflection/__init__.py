"""Reflection 子包(B4)。

`CriticAgent` + `CriticVerdict`:对主 agent 的产出强制 `VERDICT: PASS|FAIL|UNSURE`
一行 + reason 的裁判 LLM。复用 B3 wave 2 的 `AuxiliaryClient`(副 model 路由),
不引入新的 BaseProvider 子类。

`ReflectionLoop`(B4 wave 2):reflect-then-retry hook,工具失败 / 自报 fail
→ critic 介入 → critique 注入 user 消息后重跑。

模块归属理由:reflection 是 AIAgent 的行为(不是独立 surface 或 protocol),
落 `chariot/agent/reflection/`(参考 `docs/evolution-design.md` §6.4 第 5 条)。
"""

from chariot.agent.reflection.critic import CriticAgent, CriticVerdict
from chariot.agent.reflection.loop import (
    AssistantBuffer,
    ReflectionLoop,
    ReflectionRecord,
    ReflectionStep,
)

__all__ = [
    "AssistantBuffer",
    "CriticAgent",
    "CriticVerdict",
    "ReflectionLoop",
    "ReflectionRecord",
    "ReflectionStep",
]

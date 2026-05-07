"""MockProvider —— 协议无关 mock(0.6.0+)。

替代 0.5.0 `chariot/server/model/mock.py` 的 `MockModel`。差异:

- **不再手工拼 Anthropic SSE 字节**:0.5.0 MockModel 为了走 server 透传路径,
  得手工构造 SSE 帧;0.6.0 直接 yield `ChatEvent`(typed),协议无关
- **不消费 options**:`from_options(options)` 完全不读 options(沿用 0.5.0
  MockModel 行为);options 不合法不报错(mock 本来就是宽容的)

输出形态(Claude 形态序列,详见 DESIGN §3.2):

    message_start
    content_block_start(text, index=0)
    content_block_delta(text_delta) * N(echo 末轮 user content)
    content_block_stop(index=0)
    message_delta(stop_reason="end_turn", usage)
    message_stop

**不产 `stream_done`**(那是 AgentLoop 跨轮收敛职责,DESIGN §6.4.2 规定)。

模块级零自由函数(CLAUDE.md ⭐);所有逻辑收进 `MockProvider` 类。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any, Self

from chariot.agent.chat_event import ChatEvent
from chariot.agent.chat_request import ChatRequest
from chariot.providers.base import BaseProvider, BaseProviderConfig


class MockProvider(BaseProvider):
    """本地 mock Provider:不发 HTTP / 不拼 SSE,直接产 Claude 形态 ChatEvent。

    设计上:`name` / `model` 字段都是 mock 默认值(因为不需要真 LLM 后端),
    用户在 `chariot model add foo --type=mock` 时填的 name 也不会传到这里
    (`from_options(options)` 不读 options)。
    """

    _MODEL_ID = "mock-1"

    def __init__(self, config: BaseProviderConfig) -> None:
        self.config = config

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> Self:
        """`options` 不消费(沿用 0.5.0 MockModel 行为)。

        Provider 实例的 `name` / `model` 都用 mock 默认 —— 用户填的 entry name
        在外层(`AIAgent.from_db`)做路由,不需要传进 Provider 自身。
        """
        del options  # 标记参数已知未用,避免 ruff ARG003
        return cls(config=BaseProviderConfig(name="mock", model=cls._MODEL_ID))

    async def generate(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        """yield Claude 形态 ChatEvent 序列。

        `text` echo 末轮 user content(若末轮含纯文本)+ 简短 mock 标识。
        分 3 片 yield 模拟流式分块:`[mock]` / `echo: ` / `<user 内容>`。
        """
        message_id = f"msg_mock_{uuid.uuid4().hex[:8]}"
        yield ChatEvent.message_start(
            message_id=message_id,
            model=self._MODEL_ID,
            usage={"input_tokens": 0, "output_tokens": 0},
        )
        yield ChatEvent.text_block_start(index=0)
        for chunk in self._build_text_chunks(req):
            yield ChatEvent.text_delta(chunk, index=0)
        yield ChatEvent.block_stop(index=0)
        yield ChatEvent.message_delta_done(
            stop_reason="end_turn",
            usage={"output_tokens": 0},
        )
        yield ChatEvent.message_done()

    @classmethod
    def _build_text_chunks(cls, req: ChatRequest) -> list[str]:
        """构造 mock 输出文本切片(分 N 片模拟流式)。"""
        user_text = req.last_user_text() or ""
        return ["[mock]", " echo: ", user_text]

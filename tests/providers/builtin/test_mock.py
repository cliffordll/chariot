"""MockProvider 单测。

覆盖:
- `from_options(options)` 不消费 options(任意 dict 都能构造)
- `generate(req)` yield 序列形态符合 Claude 形态(message_start /
  content_block_* / message_delta / message_stop)
- text_delta 内容含 req 末轮 user content 的 echo
- **不产 `stream_done`**(那是 AgentLoop 职责)
"""

from __future__ import annotations

import pytest

from chariot.agent.chat_request import ChatRequest, Message
from chariot.providers.builtin.mock import MockProvider


@pytest.fixture
def provider() -> MockProvider:
    return MockProvider.from_options({})


@pytest.fixture
def req() -> ChatRequest:
    return ChatRequest(
        model="mock",
        messages=[Message(role="user", content="hello world")],
    )


class TestFromOptions:
    def test_empty_options(self) -> None:
        p = MockProvider.from_options({})
        assert p.config.name == "mock"
        assert p.config.model == "mock-1"

    def test_arbitrary_options_ignored(self) -> None:
        """options 不消费(任意 dict 都能构造)。"""
        p = MockProvider.from_options({"foo": "bar", "api_key": "xxx"})
        assert p.config.name == "mock"


class TestGenerateKindSequence:
    """yield 序列至少含规定的 6 种 Claude SSE event(顺序合规)。"""

    async def test_kind_order(self, provider: MockProvider, req: ChatRequest) -> None:
        kinds = [ev.kind async for ev in provider.generate(req)]
        # 起头 + 收尾必须有
        assert kinds[0] == "message_start"
        assert kinds[-1] == "message_stop"
        # 中间至少含一个 content_block_start / 多个 content_block_delta /
        # 一个 content_block_stop / 一个 message_delta
        assert "content_block_start" in kinds
        assert kinds.count("content_block_delta") >= 2
        assert "content_block_stop" in kinds
        assert "message_delta" in kinds

    async def test_no_stream_done(self, provider: MockProvider, req: ChatRequest) -> None:
        """MockProvider 不产 `stream_done`(那是 AgentLoop 职责)。"""
        kinds = [ev.kind async for ev in provider.generate(req)]
        assert "stream_done" not in kinds

    async def test_no_error(self, provider: MockProvider, req: ChatRequest) -> None:
        kinds = [ev.kind async for ev in provider.generate(req)]
        assert "error" not in kinds


class TestGenerateFieldShape:
    """逐 event 字段形态符合 Claude SSE payload。"""

    async def test_message_start_payload(self, provider: MockProvider, req: ChatRequest) -> None:
        events = [ev async for ev in provider.generate(req)]
        ms = events[0]
        assert ms.kind == "message_start"
        assert ms.message is not None
        assert ms.message["model"] == "mock-1"
        assert ms.message["role"] == "assistant"
        assert ms.message["type"] == "message"
        # message_id 应该是 mock 前缀
        assert isinstance(ms.message["id"], str)
        assert ms.message["id"].startswith("msg_mock_")

    async def test_content_block_start_text(self, provider: MockProvider, req: ChatRequest) -> None:
        events = [ev async for ev in provider.generate(req)]
        starts = [e for e in events if e.kind == "content_block_start"]
        assert len(starts) == 1
        assert starts[0].content_block == {"type": "text", "text": ""}
        assert starts[0].index == 0

    async def test_message_delta_stop_reason(
        self, provider: MockProvider, req: ChatRequest
    ) -> None:
        events = [ev async for ev in provider.generate(req)]
        deltas = [e for e in events if e.kind == "message_delta"]
        assert len(deltas) == 1
        assert deltas[0].delta is not None
        assert deltas[0].delta["stop_reason"] == "end_turn"


class TestGenerateEcho:
    """text_delta 内容含 req 末轮 user content 的 echo。"""

    async def test_echo_user_content(self, provider: MockProvider) -> None:
        req = ChatRequest(
            model="mock",
            messages=[
                Message(role="user", content="first"),
                Message(role="assistant", content="ack"),
                Message(role="user", content="last user msg"),
            ],
        )
        events = [ev async for ev in provider.generate(req)]
        text = "".join(
            e.delta["text"]  # type: ignore[index]
            for e in events
            if e.kind == "content_block_delta" and e.delta and e.delta.get("type") == "text_delta"
        )
        # 包 mock 标识 + echo + 末轮 user 内容
        assert "[mock]" in text
        assert "last user msg" in text
        assert "first" not in text  # 不 echo 历史 user 消息

    async def test_echo_empty_when_no_text(self, provider: MockProvider) -> None:
        """末轮 user content 是 image block 等非 text → echo 空字符串(不报错)。"""
        req = ChatRequest(
            model="mock",
            messages=[Message(role="user", content=[{"type": "image", "source": {}}])],
        )
        events = [ev async for ev in provider.generate(req)]
        # 至少能跑完整序列,不抛异常
        assert events[0].kind == "message_start"
        assert events[-1].kind == "message_stop"

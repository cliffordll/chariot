"""ChatRequest / Message / SystemBlock / ToolSchema 单测。

覆盖:
- frozen 不可变(改字段抛 FrozenInstanceError)
- 字段默认值符合 DESIGN §3.1
- 跟 Claude API 互操作:`ChatRequest(**claude_body)` 能构造
- `dataclasses.asdict(req)` 字段集合 ⊇ Claude API 字段集合
- 查询方法 `is_stateful` / `last_user_text` 行为
"""

from __future__ import annotations

import dataclasses

import pytest

from chariot.agent.chat_request import ChatRequest, Message, SystemBlock, ToolSchema


class TestMessageFrozen:
    """Message dataclass 不可变 + content 双形态。"""

    def test_frozen(self) -> None:
        msg = Message(role="user", content="hi")
        with pytest.raises(dataclasses.FrozenInstanceError):
            msg.role = "assistant"  # type: ignore[misc]

    def test_content_str_shorthand(self) -> None:
        msg = Message(role="user", content="hi")
        assert msg.content == "hi"

    def test_content_block_list(self) -> None:
        blocks: list[dict[str, object]] = [
            {"type": "text", "text": "hello"},
            {"type": "tool_use", "id": "toolu_x", "name": "read_file", "input": {}},
        ]
        msg = Message(role="assistant", content=blocks)
        assert isinstance(msg.content, list)
        assert msg.content[0]["type"] == "text"


class TestSystemBlockFrozen:
    def test_frozen_with_cache_control(self) -> None:
        sb = SystemBlock(type="text", text="hi", cache_control={"type": "ephemeral"})
        assert sb.cache_control == {"type": "ephemeral"}
        with pytest.raises(dataclasses.FrozenInstanceError):
            sb.text = "x"  # type: ignore[misc]

    def test_default_cache_control_none(self) -> None:
        sb = SystemBlock(type="text", text="hi")
        assert sb.cache_control is None


class TestToolSchema:
    def test_fields(self) -> None:
        schema = ToolSchema(
            name="read_file",
            description="读文件",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        )
        assert schema.name == "read_file"
        assert schema.input_schema["type"] == "object"


class TestChatRequestDefaults:
    """14 字段默认值跟 DESIGN §3.1 一致。"""

    def test_minimal_construct(self) -> None:
        req = ChatRequest(model="claude", messages=[Message(role="user", content="hi")])
        assert req.model == "claude"
        assert len(req.messages) == 1
        assert req.max_tokens == 4096
        assert req.system is None
        assert req.tools is None
        assert req.tool_choice is None
        assert req.temperature is None
        assert req.top_p is None
        assert req.top_k is None
        assert req.stop_sequences is None
        assert req.metadata is None
        assert req.thinking is None
        assert req.convo_id is None
        assert req.agent_id is None

    def test_frozen(self) -> None:
        req = ChatRequest(model="claude", messages=[Message(role="user", content="hi")])
        with pytest.raises(dataclasses.FrozenInstanceError):
            req.model = "gpt-4"  # type: ignore[misc]


class TestClaudeApiInterop:
    """ChatRequest 跟 Claude Messages API request body 1:1 平铺。"""

    def test_construct_from_claude_body_dict(self) -> None:
        """Claude API 用户能直接 `ChatRequest(**claude_body)`。"""
        claude_body: dict[str, object] = {
            "model": "claude-sonnet-4-6",
            "messages": [Message(role="user", content="hi")],
            "max_tokens": 1024,
            "system": "You are helpful",
            "temperature": 0.7,
            "top_p": 0.9,
            "stop_sequences": ["END"],
            "metadata": {"user_id": "u1"},
        }
        req = ChatRequest(**claude_body)  # type: ignore[arg-type]
        assert req.model == "claude-sonnet-4-6"
        assert req.max_tokens == 1024
        assert req.system == "You are helpful"
        assert req.temperature == 0.7
        assert req.metadata == {"user_id": "u1"}

    def test_asdict_contains_all_claude_fields(self) -> None:
        """`dataclasses.asdict(req)` 字段集合 ⊇ Claude API 字段集合。"""
        req = ChatRequest(model="claude", messages=[Message(role="user", content="hi")])
        d = dataclasses.asdict(req)
        claude_fields = {
            "model",
            "messages",
            "max_tokens",
            "system",
            "tools",
            "tool_choice",
            "temperature",
            "top_p",
            "top_k",
            "stop_sequences",
            "metadata",
            "thinking",
        }
        assert claude_fields.issubset(d.keys())

    def test_chariot_extension_fields_present(self) -> None:
        req = ChatRequest(
            model="claude",
            messages=[Message(role="user", content="hi")],
            convo_id="01H...",
            agent_id="agent_main",
        )
        d = dataclasses.asdict(req)
        assert d["convo_id"] == "01H..."
        assert d["agent_id"] == "agent_main"


class TestChatRequestQueries:
    def test_is_stateful_true(self) -> None:
        req = ChatRequest(
            model="claude",
            messages=[Message(role="user", content="hi")],
            convo_id="01H...",
        )
        assert req.is_stateful() is True

    def test_is_stateful_false(self) -> None:
        req = ChatRequest(model="claude", messages=[Message(role="user", content="hi")])
        assert req.is_stateful() is False

    def test_last_user_text_string_content(self) -> None:
        req = ChatRequest(
            model="claude",
            messages=[
                Message(role="user", content="first"),
                Message(role="assistant", content="hi"),
                Message(role="user", content="last"),
            ],
        )
        assert req.last_user_text() == "last"

    def test_last_user_text_block_content(self) -> None:
        req = ChatRequest(
            model="claude",
            messages=[
                Message(
                    role="user",
                    content=[{"type": "text", "text": "hello "}, {"type": "text", "text": "world"}],
                ),
            ],
        )
        assert req.last_user_text() == "hello world"

    def test_last_user_text_no_text_block(self) -> None:
        req = ChatRequest(
            model="claude",
            messages=[Message(role="user", content=[{"type": "image", "source": {}}])],
        )
        assert req.last_user_text() is None

    def test_last_user_text_empty(self) -> None:
        """messages 全是 assistant → 没有 user 消息 → None。"""
        req = ChatRequest(
            model="claude",
            messages=[Message(role="assistant", content="hi")],
        )
        assert req.last_user_text() is None

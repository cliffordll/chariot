"""ChatRequest / Message / SystemBlock / ToolSchema 单测。

覆盖:
- frozen 不可变(改字段抛 FrozenInstanceError)
- 字段默认值符合 DESIGN §3.1
- 跟 Claude API 互操作:结构对齐(messages / max_tokens / system 等);
  路由字段 chariot 用 `provider_name`,Claude wire 用 `model`(由 Provider 内部翻译)
- `dataclasses.asdict(req)` 字段集合包 Claude 结构字段(不含 wire `model`)
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
    """字段默认值跟 DESIGN §3.1 一致。"""

    def test_minimal_construct(self) -> None:
        req = ChatRequest(provider_name="claude", messages=[Message(role="user", content="hi")])
        assert req.provider_name == "claude"
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
        req = ChatRequest(provider_name="claude", messages=[Message(role="user", content="hi")])
        with pytest.raises(dataclasses.FrozenInstanceError):
            req.provider_name = "gpt-4"  # type: ignore[misc]


class TestClaudeApiInterop:
    """ChatRequest 结构跟 Claude Messages API 1:1;路由字段 `provider_name` 是 chariot 自己的。"""

    def test_model_field_default_none(self) -> None:
        """0.6.5+ 加回 `model: str | None = None` 字段(per-call LLM id 覆盖)。

        默认 None;Provider 内部按 `req.model or self.config.model` 决定 wire
        body["model"]。`provider_name`(路由 key)≠ `model`(LLM id)。
        """
        field_names = {f.name for f in dataclasses.fields(ChatRequest)}
        assert "model" in field_names
        assert "provider_name" in field_names
        req = ChatRequest(provider_name="claude", messages=[Message(role="user", content="hi")])
        assert req.model is None

    def test_asdict_contains_claude_structural_fields(self) -> None:
        """`dataclasses.asdict(req)` 字段集合 ⊇ Claude API 结构字段(messages /
        model / max_tokens / system / tools / sampling 等)。"""
        req = ChatRequest(provider_name="claude", messages=[Message(role="user", content="hi")])
        d = dataclasses.asdict(req)
        claude_structural_fields = {
            "messages",
            "model",
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
        assert claude_structural_fields.issubset(d.keys())
        # chariot 路由字段也在
        assert "provider_name" in d
        # 默认 None,wire body 构造时会被 Provider 滤掉(asdict 仍含)
        assert d["model"] is None

    def test_chariot_extension_fields_present(self) -> None:
        req = ChatRequest(
            provider_name="claude",
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
            provider_name="claude",
            messages=[Message(role="user", content="hi")],
            convo_id="01H...",
        )
        assert req.is_stateful() is True

    def test_is_stateful_false(self) -> None:
        req = ChatRequest(provider_name="claude", messages=[Message(role="user", content="hi")])
        assert req.is_stateful() is False

    def test_last_user_text_string_content(self) -> None:
        req = ChatRequest(
            provider_name="claude",
            messages=[
                Message(role="user", content="first"),
                Message(role="assistant", content="hi"),
                Message(role="user", content="last"),
            ],
        )
        assert req.last_user_text() == "last"

    def test_last_user_text_block_content(self) -> None:
        req = ChatRequest(
            provider_name="claude",
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
            provider_name="claude",
            messages=[Message(role="user", content=[{"type": "image", "source": {}}])],
        )
        assert req.last_user_text() is None

    def test_last_user_text_empty(self) -> None:
        """messages 全是 assistant → 没有 user 消息 → None。"""
        req = ChatRequest(
            provider_name="claude",
            messages=[Message(role="assistant", content="hi")],
        )
        assert req.last_user_text() is None

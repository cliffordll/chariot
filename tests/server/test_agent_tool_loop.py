"""Agent 工具循环测试(0.4.0 M.3)。

覆盖:
- slow path:server 注入 body.tools(client 没传时)
- 工具循环:第一轮返 tool_use → 执行工具 → 第二轮返 final → return
- 多轮工具循环
- max_iter 超限 → 400 tool_iter_exceeded
- 工具不存在(未注册或被禁) → tool_result is_error=True 进下一轮
- conversation_id auto-create + history load + persist user/assistant/tool_result
- stream + slow path:收敛后重发 model.respond(stream=True)
- client 传了 body.tools → server 不覆盖
- env CHARIOT_MAX_TOOL_ITER 生效

设计:用 SequentialMockModel,按列表逐次返回预设 response;每次 respond 拿下一个。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Self

import pytest
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.server.agent import Agent
from chariot.server.config import ToolEntry
from chariot.server.model.base import Model
from chariot.server.repository.conversation_repo import ConversationRepo
from chariot.server.service.exceptions import ServiceError
from chariot.server.tool.base import Tool

ULID_A = "01JD7K8YQXM2N8R5VF3PCWE4ZB"


# ============================================================
# 测试用 Mock Model:按预设列表逐次返响应
# ============================================================


class SequentialMockModel(Model):
    """按 _responses 列表逐次返预设响应(JSON 字符串 → Response)。

    每次 respond 拿下一项;stream=True 时返 StreamingResponse 包同样字节。
    用 calls 记录每次的 (body, stream) 供断言。
    """

    name = "spy"

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[bytes, bool]] = []

    @classmethod
    def from_config(cls, options: dict[str, Any]) -> Self:
        del options
        return cls(responses=[])

    async def respond(self, body: bytes, *, stream: bool) -> Response:
        self.calls.append((body, stream))
        if not self._responses:
            raise RuntimeError(
                f"SequentialMockModel exhausted (call #{len(self.calls)});预设响应数不够",
            )
        data = self._responses.pop(0)
        body_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
        if stream:

            async def _gen() -> Any:
                yield body_bytes

            return StreamingResponse(_gen(), media_type="text/event-stream")
        return Response(
            content=body_bytes,
            status_code=200,
            media_type="application/json",
        )


def _final_response(text: str) -> dict[str, Any]:
    """没 tool_use 的 final assistant response。"""
    return {
        "id": "msg_final",
        "type": "message",
        "role": "assistant",
        "model": "spy",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
    }


def _tool_use_response(tool_uses: list[dict[str, Any]], *, text: str = "") -> dict[str, Any]:
    """带 tool_use blocks 的 assistant response。"""
    content: list[dict[str, Any]] = []
    if text:
        content.append({"type": "text", "text": text})
    content.extend(tool_uses)
    return {
        "id": "msg_tooluse",
        "type": "message",
        "role": "assistant",
        "model": "spy",
        "content": content,
        "stop_reason": "tool_use",
    }


# ============================================================
# 测试用 Mock Tool
# ============================================================


class CountingTool(Tool):
    """记录每次调用 input,按预设列表返 tool_result content text。"""

    def __init__(self, name: str, results: list[str]) -> None:
        self.name = name
        self._results = list(results)
        self.calls: list[dict[str, Any]] = []

    @classmethod
    def from_config(cls, entry: ToolEntry) -> Self:  # pragma: no cover -- 测试不走
        return cls(name=entry.name, results=[])

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "test counting tool",
            "input_schema": {
                "type": "object",
                "properties": {"x": {"type": "string"}},
            },
        }

    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(input)
        text = self._results.pop(0) if self._results else "ok"
        return {
            "type": "tool_result",
            "content": [{"type": "text", "text": text}],
        }


# ============================================================
# Helper:构造一个带 model + tool 的 agent
# ============================================================


MakeTestAgent = Callable[[dict[str, Model]], Agent]


def _setup_agent_with_tools(
    make_test_agent: MakeTestAgent,
    *,
    model: Model,
    tools: dict[str, Tool],
    model_name: str = "m",
) -> Agent:
    agent = make_test_agent({model_name: model})
    agent.tools = dict(tools)
    return agent


# ============================================================
# 工具循环主路径
# ============================================================


async def test_tool_injection_when_client_omits(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
) -> None:
    """client 没传 body.tools → server 注入所有 enabled tool 的 schema 进 body."""
    tool = CountingTool("read_file", ["file content"])
    model = SequentialMockModel(
        [_final_response("ok done")],  # 第一轮就收敛(不返 tool_use)
    )
    agent = _setup_agent_with_tools(make_test_agent, model=model, tools={"read_file": tool})

    body = json.dumps({"model": "m", "messages": [{"role": "user", "content": "hi"}]}).encode(
        "utf-8"
    )
    await agent.handle(body)

    # 验:model 收到的 body 含 tools 数组(server 注入)
    assert len(model.calls) == 1
    sent_body = json.loads(model.calls[0][0])
    assert "tools" in sent_body
    assert any(t["name"] == "read_file" for t in sent_body["tools"])


async def test_client_tools_not_overridden(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
) -> None:
    """client 传了 body.tools → server 不覆盖(client 优先)。"""
    tool = CountingTool("read_file", [])
    model = SequentialMockModel([_final_response("done")])
    agent = _setup_agent_with_tools(make_test_agent, model=model, tools={"read_file": tool})

    client_tools = [{"name": "client_custom", "description": "custom", "input_schema": {}}]
    body = json.dumps({"model": "m", "tools": client_tools, "messages": []}).encode("utf-8")
    await agent.handle(body)

    sent_body = json.loads(model.calls[0][0])
    assert sent_body["tools"] == client_tools  # 原样保留,server 没插 read_file


async def test_single_tool_call_then_final(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
) -> None:
    """轮 1:assistant 返 tool_use → 执行工具 → 轮 2:assistant 返 final text。"""
    tool = CountingTool("read_file", ["文件内容"])
    model = SequentialMockModel(
        [
            _tool_use_response(
                [
                    {
                        "type": "tool_use",
                        "id": "tu_1",
                        "name": "read_file",
                        "input": {"path": "x.txt"},
                    }
                ],
            ),
            _final_response("基于文件内容,答案是 X"),
        ],
    )
    agent = _setup_agent_with_tools(make_test_agent, model=model, tools={"read_file": tool})

    body = json.dumps({"model": "m", "messages": [{"role": "user", "content": "看 x.txt"}]}).encode(
        "utf-8"
    )
    resp = await agent.handle(body)

    # 验:工具被调用一次 + 输入正确
    assert len(tool.calls) == 1
    assert tool.calls[0] == {"path": "x.txt"}
    # 验:model 被调用两次
    assert len(model.calls) == 2
    # 第二轮 body.messages 应含 tool_result
    second_body = json.loads(model.calls[1][0])
    second_msgs = second_body["messages"]
    last_msg = second_msgs[-1]
    assert last_msg["role"] == "user"
    assert last_msg["content"][0]["type"] == "tool_result"
    assert last_msg["content"][0]["tool_use_id"] == "tu_1"
    # 终轮响应原样返
    assert resp.status_code == 200


async def test_multi_round_tool_loop(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
) -> None:
    """3 轮工具循环:tool_use → exec → tool_use → exec → final。"""
    tool = CountingTool("read_file", ["内容1", "内容2"])
    model = SequentialMockModel(
        [
            _tool_use_response(
                [{"type": "tool_use", "id": "tu_1", "name": "read_file", "input": {"path": "a"}}],
            ),
            _tool_use_response(
                [{"type": "tool_use", "id": "tu_2", "name": "read_file", "input": {"path": "b"}}],
            ),
            _final_response("综合 a / b 内容,答案"),
        ],
    )
    agent = _setup_agent_with_tools(make_test_agent, model=model, tools={"read_file": tool})

    body = json.dumps({"model": "m", "messages": []}).encode("utf-8")
    await agent.handle(body)

    assert len(tool.calls) == 2
    assert len(model.calls) == 3


async def test_unknown_tool_returns_is_error(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
) -> None:
    """LLM 调用未注册的工具 → server 返 is_error tool_result,继续循环。"""
    model = SequentialMockModel(
        [
            _tool_use_response(
                [{"type": "tool_use", "id": "tu_1", "name": "nope", "input": {}}],
            ),
            _final_response("ok"),
        ],
    )
    agent = _setup_agent_with_tools(make_test_agent, model=model, tools={})

    body = json.dumps({"model": "m", "tools": [{"name": "nope"}], "messages": []}).encode("utf-8")
    await agent.handle(body)

    second_body = json.loads(model.calls[1][0])
    last_msg = second_body["messages"][-1]
    assert last_msg["content"][0]["is_error"] is True
    assert "nope" in last_msg["content"][0]["content"][0]["text"]


async def test_max_iter_exceeded_raises(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配 max_iter=2,Mock 一直返 tool_use → 第 2 轮超限 → 400 tool_iter_exceeded。"""
    monkeypatch.setenv("CHARIOT_MAX_TOOL_ITER", "2")
    tool = CountingTool("t", ["a", "b", "c"])
    model = SequentialMockModel(
        [
            _tool_use_response(
                [{"type": "tool_use", "id": f"tu_{i}", "name": "t", "input": {}}],
            )
            for i in range(5)
        ],
    )
    agent = _setup_agent_with_tools(make_test_agent, model=model, tools={"t": tool})

    body = json.dumps({"model": "m", "messages": []}).encode("utf-8")
    with pytest.raises(ServiceError) as exc:
        await agent.handle(body)
    assert exc.value.code == "tool_iter_exceeded"
    assert exc.value.status == 400


async def test_env_max_iter_default_when_unset(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """env 没设 → 默认 10。"""
    monkeypatch.delenv("CHARIOT_MAX_TOOL_ITER", raising=False)
    assert Agent._max_tool_iter() == 10


async def test_env_max_iter_invalid_falls_back(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非数字 / 非正整数 → fallback 默认。"""
    monkeypatch.setenv("CHARIOT_MAX_TOOL_ITER", "not-a-number")
    assert Agent._max_tool_iter() == 10
    monkeypatch.setenv("CHARIOT_MAX_TOOL_ITER", "0")
    assert Agent._max_tool_iter() == 10


# ============================================================
# Conversation 持久化(slow path with conversation_id + session)
# ============================================================


async def test_handle_with_conversation_persists_messages(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
) -> None:
    """带 conversation_id → user / assistant / tool_result 都被 persist。"""
    tool = CountingTool("t", ["tool result text"])
    model = SequentialMockModel(
        [
            _tool_use_response(
                [{"type": "tool_use", "id": "tu_1", "name": "t", "input": {}}],
            ),
            _final_response("done"),
        ],
    )
    agent = _setup_agent_with_tools(make_test_agent, model=model, tools={"t": tool})

    body = json.dumps(
        {"model": "m", "messages": [{"role": "user", "content": "hi"}]},
    ).encode("utf-8")
    await agent.handle(body, session=session, conversation_id=ULID_A)

    repo = ConversationRepo(session)
    msgs = await repo.load_messages_as_anthropic(ULID_A)
    # 应该有 4 条 messages:
    # 0: user "hi"
    # 1: assistant 含 tool_use
    # 2: user 含 tool_result
    # 3: assistant 含 final text
    assert len(msgs) == 4
    assert msgs[0]["role"] == "user" and msgs[0]["content"] == "hi"
    assert msgs[1]["role"] == "assistant"
    assert msgs[2]["role"] == "user"
    assert msgs[3]["role"] == "assistant"


async def test_handle_with_conversation_loads_history(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
) -> None:
    """已有 conversation 历史 → 第一轮 model 收到的 body.messages 含历史 + 新消息。"""
    repo = ConversationRepo(session)
    await repo.create(ULID_A)
    await repo.append_message(ULID_A, "user", "上轮的问题")
    await repo.append_message(ULID_A, "assistant", "上轮的答", model_name="m")

    model = SequentialMockModel([_final_response("现在的答案")])
    agent = _setup_agent_with_tools(make_test_agent, model=model, tools={})

    body = json.dumps(
        {"model": "m", "tools": [], "messages": [{"role": "user", "content": "新问题"}]},
    ).encode("utf-8")
    await agent.handle(body, session=session, conversation_id=ULID_A)

    sent_body = json.loads(model.calls[0][0])
    sent_msgs = sent_body["messages"]
    assert len(sent_msgs) == 3  # 历史 2 + 新 1
    assert sent_msgs[0]["content"] == "上轮的问题"
    assert sent_msgs[1]["content"] == "上轮的答"
    assert sent_msgs[2]["content"] == "新问题"


async def test_handle_with_conversation_auto_creates(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
) -> None:
    """conversation_id 不存在 → ensure_exists 自动创建。"""
    model = SequentialMockModel([_final_response("ok")])
    agent = _setup_agent_with_tools(make_test_agent, model=model, tools={})

    body = json.dumps({"model": "m", "tools": [], "messages": []}).encode("utf-8")
    await agent.handle(body, session=session, conversation_id=ULID_A)

    repo = ConversationRepo(session)
    conv = await repo.get(ULID_A)
    assert conv is not None
    assert conv.id == ULID_A


async def test_handle_with_conversation_updates_last_model(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
) -> None:
    """assistant 落库时同步 conversations.last_model。"""
    model = SequentialMockModel([_final_response("ok")])
    agent = _setup_agent_with_tools(make_test_agent, model=model, tools={}, model_name="claude")

    body = json.dumps(
        {"model": "claude", "tools": [], "messages": [{"role": "user", "content": "hi"}]},
    ).encode("utf-8")
    await agent.handle(body, session=session, conversation_id=ULID_A)

    repo = ConversationRepo(session)
    conv = await repo.get(ULID_A)
    assert conv is not None
    assert conv.last_model == "claude"


# ============================================================
# Stream 重发
# ============================================================


async def test_stream_in_slow_path_replays_with_stream_true(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
) -> None:
    """slow path + client 要 stream → 收敛后再调一次 model.respond(stream=True)."""
    tool = CountingTool("t", ["res"])
    model = SequentialMockModel(
        [
            _tool_use_response(
                [{"type": "tool_use", "id": "tu_1", "name": "t", "input": {}}],
            ),
            _final_response("done"),
            _final_response("done streamed"),  # 第 3 次:重发拿 stream
        ],
    )
    agent = _setup_agent_with_tools(make_test_agent, model=model, tools={"t": tool})

    body = json.dumps({"model": "m", "stream": True, "messages": []}).encode("utf-8")
    resp = await agent.handle(body)

    # 验:model 被调 3 次,最后一次 stream=True
    assert len(model.calls) == 3
    assert model.calls[0][1] is False  # tool_use 轮
    assert model.calls[1][1] is False  # 收敛轮
    assert model.calls[2][1] is True  # 重发 stream
    assert isinstance(resp, StreamingResponse)


async def test_stream_in_fast_path_no_replay(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
) -> None:
    """fast path + stream=True → 单次调用 stream=True,无重发。"""
    model = SequentialMockModel([_final_response("ok")])
    agent = _setup_agent_with_tools(make_test_agent, model=model, tools={})

    body = json.dumps({"model": "m", "stream": True, "messages": []}).encode("utf-8")
    await agent.handle(body)

    assert len(model.calls) == 1
    assert model.calls[0][1] is True

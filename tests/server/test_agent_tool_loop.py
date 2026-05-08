"""Agent 流式工具循环测试(0.5.0 S.1)。

跟 0.4.0 比关键变化:agent 全程 streaming —— 上游 Model 永远以 stream=True 调,
mock model 必须吐 Anthropic Messages 协议的 SSE 字节流。一条 HTTP 响应里串多
个 message_start ... message_stop 块(每轮一对)+ server 在轮间合成 tool_result
message 的 SSE 帧。

覆盖:
- slow path:server 注入 body.tools(client 没传时)
- 单轮工具循环 / 多轮 / 未知工具 / max_iter 超限
- convo_id auto-create + history load + persist user/assistant/tool_result
- stream client:返 StreamingResponse,流里含多 message_start 块
- 非 stream client:server 内部 streaming + drain 后重建 Anthropic 非流 JSON
- env CHARIOT_MAX_TOOL_ITER 生效
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any, Self

import pytest
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from chariot.agent.config import ToolEntry
from chariot.repos.convo_repo import ConvoRepo
from chariot.server.agent import Agent
from chariot.server.model.base import Model
from chariot.server.service.exceptions import ServiceError
from chariot.shared.sse import SseParser
from chariot.tools.base import BaseTool

ULID_A = "01JD7K8YQXM2N8R5VF3PCWE4ZB"


# ============================================================
# SSE 构造工具
# ============================================================


def _sse_event(name: str, data: dict[str, Any]) -> bytes:
    """跟 chariot.server.agent._format_sse_event / mock._sse_event 同款。"""
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


def _build_turn_sse(
    content_blocks: list[dict[str, Any]],
    *,
    stop_reason: str = "end_turn",
    msg_id: str = "msg_test",
    model: str = "spy",
) -> list[bytes]:
    """构造一轮 LLM 响应的完整 SSE 帧序列(Anthropic Messages 协议)。

    content_blocks 每条:
      {"type": "text", "text": "..."}              → 一个 text block
      {"type": "tool_use", "id": "...", "name": "...", "input": {...}}  → 一个 tool_use block
    """
    frames: list[bytes] = []
    frames.append(
        _sse_event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": msg_id,
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            },
        ),
    )
    for i, block in enumerate(content_blocks):
        btype = block["type"]
        if btype == "text":
            frames.append(
                _sse_event(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": i,
                        "content_block": {"type": "text", "text": ""},
                    },
                ),
            )
            frames.append(
                _sse_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": i,
                        "delta": {"type": "text_delta", "text": block["text"]},
                    },
                ),
            )
            frames.append(
                _sse_event(
                    "content_block_stop",
                    {"type": "content_block_stop", "index": i},
                ),
            )
        elif btype == "tool_use":
            frames.append(
                _sse_event(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": i,
                        "content_block": {
                            "type": "tool_use",
                            "id": block["id"],
                            "name": block["name"],
                            "input": {},
                        },
                    },
                ),
            )
            frames.append(
                _sse_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": i,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": json.dumps(block["input"]),
                        },
                    },
                ),
            )
            frames.append(
                _sse_event(
                    "content_block_stop",
                    {"type": "content_block_stop", "index": i},
                ),
            )
    frames.append(
        _sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": 10},
            },
        ),
    )
    frames.append(_sse_event("message_stop", {"type": "message_stop"}))
    return frames


# ============================================================
# 测试用 Mock Model:按预设列表逐次返 SSE 流
# ============================================================


class SequentialMockModel(Model):
    """按 _turns 列表逐次返 SSE 流。每次 respond 取下一项打包 StreamingResponse。

    `_turns[i]` 是 list[dict]:这一轮的 content blocks(text / tool_use)。
    每次 respond 用它构造 _build_turn_sse 字节然后包 StreamingResponse。
    """

    name = "spy"

    def __init__(self, turns: list[list[dict[str, Any]]]) -> None:
        self._turns = list(turns)
        self.calls: list[tuple[bytes, bool]] = []

    @classmethod
    def from_config(cls, options: dict[str, Any]) -> Self:
        del options
        return cls(turns=[])

    async def respond(self, body: bytes, *, stream: bool) -> Response:
        self.calls.append((body, stream))
        if not self._turns:
            raise RuntimeError(
                f"SequentialMockModel exhausted (call #{len(self.calls)});预设 turns 数不够",
            )
        blocks = self._turns.pop(0)
        # 决定 stop_reason:含 tool_use → "tool_use",否则 "end_turn"
        has_tu = any(b.get("type") == "tool_use" for b in blocks)
        sse_frames = _build_turn_sse(
            blocks,
            stop_reason="tool_use" if has_tu else "end_turn",
        )

        async def _gen() -> AsyncIterator[bytes]:
            for f in sse_frames:
                yield f

        # 0.5.0 起 Agent slow path 永远 stream=True 调上游
        return StreamingResponse(_gen(), media_type="text/event-stream")


# ============================================================
# 测试用 Mock Tool
# ============================================================


class CountingTool(BaseTool):
    """记录每次调用 input,按预设列表返 tool_result content text。"""

    def __init__(self, name: str, results: list[str]) -> None:
        self.name = name
        self._results = list(results)
        self.calls: list[dict[str, Any]] = []

    @classmethod
    def create(cls, entry: ToolEntry) -> Self:  # pragma: no cover -- 测试不走
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
    tools: dict[str, BaseTool],
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
    """client 没传 body.tools → server 注入所有 enabled tool 的 schema 进 body.

    带 convo_id 走 slow path(否则 fast path 不进流式逻辑)。
    """
    tool = CountingTool("read_file", ["file content"])
    model = SequentialMockModel([[{"type": "text", "text": "ok done"}]])  # 一轮收敛
    agent = _setup_agent_with_tools(make_test_agent, model=model, tools={"read_file": tool})

    body = json.dumps({"model": "m", "messages": [{"role": "user", "content": "hi"}]}).encode(
        "utf-8"
    )
    await agent.handle(body, session=session, convo_id=ULID_A)

    assert len(model.calls) == 1
    sent_body = json.loads(model.calls[0][0])
    assert "tools" in sent_body
    assert any(t["name"] == "read_file" for t in sent_body["tools"])
    # 0.5.0 slow path 永远 stream=True 给上游
    assert model.calls[0][1] is True


async def test_client_tools_not_overridden(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
) -> None:
    """client 传了 body.tools → server 不覆盖(client 优先)。"""
    tool = CountingTool("read_file", [])
    model = SequentialMockModel([[{"type": "text", "text": "done"}]])
    agent = _setup_agent_with_tools(make_test_agent, model=model, tools={"read_file": tool})

    client_tools = [{"name": "client_custom", "description": "custom", "input_schema": {}}]
    body = json.dumps({"model": "m", "tools": client_tools, "messages": []}).encode("utf-8")
    await agent.handle(body)

    sent_body = json.loads(model.calls[0][0])
    assert sent_body["tools"] == client_tools


async def test_single_tool_call_then_final(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
) -> None:
    """轮 1:assistant 返 tool_use → 执行工具 → 轮 2:assistant 返 final text。

    非 stream client 拿到的是重建后的 Anthropic 非流 JSON。
    """
    tool = CountingTool("read_file", ["文件内容"])
    model = SequentialMockModel(
        [
            [
                {
                    "type": "tool_use",
                    "id": "tu_1",
                    "name": "read_file",
                    "input": {"path": "x.txt"},
                },
            ],
            [{"type": "text", "text": "基于文件内容,答案是 X"}],
        ],
    )
    agent = _setup_agent_with_tools(make_test_agent, model=model, tools={"read_file": tool})

    body = json.dumps({"model": "m", "messages": [{"role": "user", "content": "看 x.txt"}]}).encode(
        "utf-8"
    )
    resp = await agent.handle(body)

    # 工具被调用一次 + 输入正确
    assert len(tool.calls) == 1
    assert tool.calls[0] == {"path": "x.txt"}
    # model 被调用两次,都是 stream=True
    assert len(model.calls) == 2
    assert all(c[1] is True for c in model.calls)
    # 第二轮 body.messages 应含 server 拼好的 tool_result
    second_body = json.loads(model.calls[1][0])
    second_msgs = second_body["messages"]
    last_msg = second_msgs[-1]
    assert last_msg["role"] == "user"
    assert last_msg["content"][0]["type"] == "tool_result"
    assert last_msg["content"][0]["tool_use_id"] == "tu_1"
    # 非 stream client → 重建的 Anthropic JSON 响应
    assert isinstance(resp, Response)
    assert not isinstance(resp, StreamingResponse)
    assert resp.status_code == 200
    final_body = json.loads(bytes(resp.body))
    assert final_body["role"] == "assistant"
    assert final_body["content"][0]["text"] == "基于文件内容,答案是 X"


async def test_multi_round_tool_loop(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
) -> None:
    """3 轮工具循环:tool_use → exec → tool_use → exec → final。"""
    tool = CountingTool("read_file", ["内容1", "内容2"])
    model = SequentialMockModel(
        [
            [{"type": "tool_use", "id": "tu_1", "name": "read_file", "input": {"path": "a"}}],
            [{"type": "tool_use", "id": "tu_2", "name": "read_file", "input": {"path": "b"}}],
            [{"type": "text", "text": "综合 a / b 内容,答案"}],
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
            [{"type": "tool_use", "id": "tu_1", "name": "nope", "input": {}}],
            [{"type": "text", "text": "ok"}],
        ],
    )
    agent = _setup_agent_with_tools(make_test_agent, model=model, tools={})

    body = json.dumps({"model": "m", "tools": [{"name": "nope"}], "messages": []}).encode("utf-8")
    await agent.handle(body)

    second_body = json.loads(model.calls[1][0])
    last_msg = second_body["messages"][-1]
    assert last_msg["content"][0]["is_error"] is True
    assert "nope" in last_msg["content"][0]["content"][0]["text"]


async def test_max_iter_exceeded_raises_for_non_stream(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配 max_iter=2,Mock 一直返 tool_use → 第 2 轮超限 → 非 stream client raise 400。"""
    monkeypatch.setenv("CHARIOT_MAX_TOOL_ITER", "2")
    tool = CountingTool("t", ["a", "b", "c"])
    model = SequentialMockModel(
        [[{"type": "tool_use", "id": f"tu_{i}", "name": "t", "input": {}}] for i in range(5)],
    )
    agent = _setup_agent_with_tools(make_test_agent, model=model, tools={"t": tool})

    body = json.dumps({"model": "m", "messages": []}).encode("utf-8")
    with pytest.raises(ServiceError) as exc:
        await agent.handle(body)
    assert exc.value.code == "tool_iter_exceeded"
    assert exc.value.status == 400


async def test_max_iter_exceeded_emits_error_event_for_stream(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stream client 超限 → 流里含 `event: error`(SSE 协议原生),关流 graceful。"""
    monkeypatch.setenv("CHARIOT_MAX_TOOL_ITER", "2")
    tool = CountingTool("t", ["a", "b", "c"])
    model = SequentialMockModel(
        [[{"type": "tool_use", "id": f"tu_{i}", "name": "t", "input": {}}] for i in range(5)],
    )
    agent = _setup_agent_with_tools(make_test_agent, model=model, tools={"t": tool})

    body = json.dumps({"model": "m", "stream": True, "messages": []}).encode("utf-8")
    resp = await agent.handle(body)
    assert isinstance(resp, StreamingResponse)

    chunks: list[bytes] = []
    async for chunk in resp.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else bytes(chunk))  # type: ignore[arg-type]
    full = b"".join(chunks)
    # 流尾应有一条 event: error
    events: list[str] = []

    async def _byte_iter() -> AsyncIterator[bytes]:
        yield full

    async for event_name, data in SseParser.iter_frames(_byte_iter()):
        if event_name == "error":
            events.append(data.get("error", {}).get("type", ""))
    assert "tool_iter_exceeded" in events


async def test_env_max_iter_default_when_unset(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """env 没设 → 默认 10。"""
    del make_test_agent
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
# Conversation 持久化(slow path with convo_id + session)
# ============================================================


async def test_handle_with_conversation_persists_messages(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
) -> None:
    """带 convo_id → user / assistant / tool_result 都被 persist。"""
    tool = CountingTool("t", ["tool result text"])
    model = SequentialMockModel(
        [
            [{"type": "tool_use", "id": "tu_1", "name": "t", "input": {}}],
            [{"type": "text", "text": "done"}],
        ],
    )
    agent = _setup_agent_with_tools(make_test_agent, model=model, tools={"t": tool})

    body = json.dumps(
        {"model": "m", "messages": [{"role": "user", "content": "hi"}]},
    ).encode("utf-8")
    await agent.handle(body, session=session, convo_id=ULID_A)

    repo = ConvoRepo(session)
    msgs = await repo.load_messages_as_anthropic(ULID_A)
    # 4 条 messages:
    # 0: user "hi"
    # 1: assistant 含 tool_use
    # 2: user 含 tool_result(server 拼)
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
    repo = ConvoRepo(session)
    await repo.create(ULID_A)
    await repo.append_message(ULID_A, "user", "上轮的问题")
    await repo.append_message(ULID_A, "assistant", "上轮的答", provider_name="m")

    model = SequentialMockModel([[{"type": "text", "text": "现在的答案"}]])
    agent = _setup_agent_with_tools(make_test_agent, model=model, tools={})

    body = json.dumps(
        {"model": "m", "tools": [], "messages": [{"role": "user", "content": "新问题"}]},
    ).encode("utf-8")
    await agent.handle(body, session=session, convo_id=ULID_A)

    sent_body = json.loads(model.calls[0][0])
    sent_msgs = sent_body["messages"]
    assert len(sent_msgs) == 3
    assert sent_msgs[0]["content"] == "上轮的问题"
    assert sent_msgs[1]["content"] == "上轮的答"
    assert sent_msgs[2]["content"] == "新问题"


async def test_handle_with_conversation_auto_creates(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
) -> None:
    """convo_id 不存在 → ensure_exists 自动创建。"""
    model = SequentialMockModel([[{"type": "text", "text": "ok"}]])
    agent = _setup_agent_with_tools(make_test_agent, model=model, tools={})

    body = json.dumps({"model": "m", "tools": [], "messages": []}).encode("utf-8")
    await agent.handle(body, session=session, convo_id=ULID_A)

    repo = ConvoRepo(session)
    conv = await repo.get(ULID_A)
    assert conv is not None
    assert conv.id == ULID_A


async def test_handle_with_conversation_updates_last_model(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
) -> None:
    """assistant 落库时同步 conversations.last_model。"""
    model = SequentialMockModel([[{"type": "text", "text": "ok"}]])
    agent = _setup_agent_with_tools(make_test_agent, model=model, tools={}, model_name="claude")

    body = json.dumps(
        {"model": "claude", "tools": [], "messages": [{"role": "user", "content": "hi"}]},
    ).encode("utf-8")
    await agent.handle(body, session=session, convo_id=ULID_A)

    repo = ConvoRepo(session)
    conv = await repo.get(ULID_A)
    assert conv is not None
    assert conv.last_model == "claude"


# ============================================================
# Stream client(0.5.0 不再有"重发"模式;直接转发流)
# ============================================================


async def test_stream_client_gets_streaming_response(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
) -> None:
    """stream=True client → 返 StreamingResponse;model 调用次数 = 工具循环轮数(不 +1)。"""
    tool = CountingTool("t", ["res"])
    model = SequentialMockModel(
        [
            [{"type": "tool_use", "id": "tu_1", "name": "t", "input": {}}],
            [{"type": "text", "text": "done"}],
        ],
    )
    agent = _setup_agent_with_tools(make_test_agent, model=model, tools={"t": tool})

    body = json.dumps({"model": "m", "stream": True, "messages": []}).encode("utf-8")
    resp = await agent.handle(body)

    assert isinstance(resp, StreamingResponse)
    # drain 流
    chunks: list[bytes] = []
    async for chunk in resp.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else bytes(chunk))  # type: ignore[arg-type]

    # 0.5.0 行为:model 调 2 次(无"重发"),都 stream=True
    assert len(model.calls) == 2
    assert all(c[1] is True for c in model.calls)
    # 流里应含至少 2 个 message_start(轮 1 + 轮 2)+ 1 个 server 合成的 tool_result message
    full = b"".join(chunks)

    async def _byte_iter() -> AsyncIterator[bytes]:
        yield full

    msg_starts: list[str] = []
    async for event_name, data in SseParser.iter_frames(_byte_iter()):
        if event_name == "message_start":
            msg = data.get("message", {})
            msg_starts.append(msg.get("role", ""))

    # turn1 assistant + 合成 tool_result(role=user) + turn2 assistant
    assert msg_starts == ["assistant", "user", "assistant"]


async def test_stream_in_fast_path_no_loop(
    session: AsyncSession,
    make_test_agent: MakeTestAgent,
) -> None:
    """fast path + stream=True → 单次调用 stream=True,直接透传(不进 slow path)。"""
    del session
    model = SequentialMockModel([[{"type": "text", "text": "ok"}]])
    agent = _setup_agent_with_tools(make_test_agent, model=model, tools={})

    body = json.dumps({"model": "m", "stream": True, "messages": []}).encode("utf-8")
    await agent.handle(body)

    # fast path:1 次调用,stream=True
    assert len(model.calls) == 1
    assert model.calls[0][1] is True
